"""Decision queue: one durable queue for everything blocked on a judgment call.

Factory design component 4 (basicly-kjc5.4, sections 7.1/7.3): needs-input
facts, rework escalations, checkpoint requests, and stall flags all become
items in **one** queue instead of four ad-hoc surfaces. Items persist as
``[harness-decision]`` comment markers on the affected record — the same
durable, attributable pattern as ``[harness-policy]`` — so the queue needs no
side-state and ``loop decisions`` is a pure read over the tracker.

Two consumers, per the design's session modes:

- **Interactive**: the notify hook (``[policy] notify_command``) fires per new
  human-required item, and a human answers via ``basicly loop answer``.
- **Autonomous**: the supervisor (kjc5.7) invokes the **decider agent** per
  item (:func:`invoke_decider`) with corpus-bounded authority — it may answer
  only what is derivable from the session's intake corpus (the root issue's
  description plus its ``agent_context`` attachment), must return the
  structured verdict ``{decision, rationale, confidence, abstain}``, and an
  abstention routes the item to the human. ``[policy] decider_max_decisions``
  caps delegated answers per session as the runaway-loop guard. The corpus
  bound and the cap are contract-level guards on a headless agent, not a
  sandbox — the same mitigation-not-boundary stance as the policy tripwires.

Item ids are content-derived (``<issue>#<hash>``), so re-enqueueing the same
blocked fact is idempotent — a crash-looping lane cannot flood the queue.
"""

from __future__ import annotations

import contextlib
import subprocess
import threading
from pathlib import Path

from . import policy, run_record, runner, tracker
from .config import (
    PolicyConfig,
    load_policy_config,
    load_runner_config,
)
from .decider_contract import (
    DECIDER_BY_PREFIX,
    DeciderVerdict,
    decider_prompt,
    intake_corpus,
    parse_verdict,
)
from .decision_marker import (
    BY_TOKEN,
    KINDS,
    DecisionItem,
    decision_id_for,
    items_by_id,
    render_answer,
    render_enqueue,
    split_decision_id,
)
from .tracker import add_comment as _add_comment

# Enqueue and delegated-answer recording are read-then-write over br comments, so
# the supervisor's concurrent lanes (basicly-kjc5.6) could double-enqueue the same
# fact — two notifications for one decision — or overshoot decider_max_decisions by
# reading the count before another thread recorded its answer. Serialize
# same-process writers here; cross-process races stay accepted, matching the
# run-record stance (basicly-kjc5.17).
_QUEUE_LOCK = threading.Lock()


def enqueue(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    issue_id: str,
    kind: str,
    question: str,
    detail: str = "",
    *,
    human_required: bool = True,
) -> DecisionItem:
    """Persist a queue item on *issue_id*; idempotent per (issue, kind, question).

    A re-enqueue of an already-recorded item (answered or not) returns the
    existing item without a duplicate marker or a duplicate notification. The
    notify hook fires only for *human_required* items (design 7.3) — the
    supervisor passes False when it will try the decider first.

    The scan-then-write is guarded by :data:`_QUEUE_LOCK`, so the idempotence
    above holds under the supervisor's concurrent lanes: without it two threads
    both read "not queued" and both write, producing one duplicate marker and two
    notifications for a single fact (basicly-kjc5.17).
    """
    if kind not in KINDS:
        raise ValueError(f"unknown decision kind {kind!r}; expected one of {KINDS}")
    with _QUEUE_LOCK:
        items = items_by_id(repo_root, issue_id)
        generation = 1
        while True:
            decision_id = decision_id_for(issue_id, kind, question, generation)
            existing = items.get(decision_id)
            if existing is None:
                break
            if existing.pending:
                # Idempotent: the same blocked fact is already queued and notified.
                return existing
            # Answered, yet the fact blocked again (wrong answer, or it never
            # reached a re-dispatch): re-open under the next generation instead of
            # silently reporting an empty queue while the loop stays wedged.
            generation += 1
        _add_comment(repo_root, issue_id, render_enqueue(decision_id, kind, question, detail))
        item = DecisionItem(
            decision_id=decision_id,
            issue_id=issue_id,
            kind=kind,
            question=question,
            detail=detail,
        )
    # Notify outside the lock: the hook is a user-configured subprocess, so a slow
    # or hanging notifier must not stall every other lane's enqueue.
    if human_required:
        _notify(repo_root, item)
    return item


def answer(  # noqa: PLR0913 — mirrors the CLI surface
    repo_root: Path,
    decision_id: str,
    text: str,
    *,
    by: str,
    rationale: str | None = None,
    confidence: float | None = None,
) -> DecisionItem:
    """Record *text* as the answer to *decision_id*, attributed to *by*.

    The answer is a second marker with the same id on the same bead — recorded
    in place, so the queue read stays a pure scan. Raises when the item does
    not exist (an answer must land on a real question), is already answered
    (the first answer wins; a second answerer must read it, not overwrite it),
    or *by* is not a strict single token (a crafted attribution could inject
    header fields or corrupt the marker — see :data:`decision_marker.BY_TOKEN`). Optional
    *rationale*/*confidence* persist the decider's audit trail (design 7.1)
    in the payload for decision review.

    Recording an answer is also what closes the item's wait interval — the queue
    is the harness's own measure of how long it sat blocked (basicly-kjc5.51).
    """
    if not BY_TOKEN.match(by):
        raise ValueError(
            f"attribution {by!r} must match {BY_TOKEN.pattern} "
            "(single token; no spaces, '=', or newlines)"
        )
    issue_id, _ = split_decision_id(decision_id)
    item = items_by_id(repo_root, issue_id).get(decision_id)
    if item is None:
        raise ValueError(f"no decision {decision_id!r} recorded on {issue_id}")
    if not item.pending:
        raise ValueError(f"decision {decision_id!r} was already answered by {item.answered_by}")
    _add_comment(
        repo_root,
        issue_id,
        render_answer(decision_id, text, by=by, rationale=rationale, confidence=confidence),
    )
    _record_wait(repo_root, item, by)
    return DecisionItem(
        decision_id=decision_id,
        issue_id=issue_id,
        kind=item.kind,
        question=item.question,
        detail=item.detail,
        answer=text,
        answered_by=by,
        queued_at=item.queued_at,
    )


def _record_wait(repo_root: Path, item: DecisionItem, by: str) -> None:
    """Record how long the queue held *item* before *by* answered it (D11).

    The interval needs no new state: the enqueue marker's tracker stamp is the
    start, and the answer just written is the end. An item whose marker carries no
    usable stamp records nothing (:func:`policy.record_wait` decides that, so the
    rule lives in one place) rather than a fabricated interval — the wait meter
    under-reports before it invents.

    A delegated answer is recorded as one: the decider disposing of an item is the
    wait an autonomy grant removed from the human's column, which is the whole
    point of measuring the two apart. The engine retiring its own moot question
    counts the same way and for the same reason — no human waited on it, so charging
    the interval to the human column would overstate the very number the wait meter
    exists to measure (basicly-jr0l.52).
    """
    policy.record_wait(
        repo_root,
        item.issue_id,
        wait_id=item.decision_id,
        kind="decision",
        subject=item.kind,
        requested_at=item.queued_at,
        by=by,
        delegated=by.startswith(DECIDER_BY_PREFIX) or by == ENGINE_BY,
    )


def pending(repo_root: Path, root_issue: str) -> tuple[DecisionItem, ...]:
    """The session's unanswered items on still-open beads, root first then the tree.

    Closed beads are excluded because their questions are moot by construction, and
    reporting them was not only cosmetic (basicly-jr0l.24): every item here is handed
    to the decider by ``supervise.delegate_decisions``, so a stale one spent tokens
    deciding finished work, and :func:`has_pending` holds a lane, so one on a reopened
    bead could wedge a lane with nothing to wait for. Five such items sat on ``main``
    after the 2026-08-01 proof run, on beads that had shipped and closed hours earlier.

    Statuses come from the committed export — one file read for the whole tracker
    rather than a ``br show`` per bead. Its staleness can only run one way: an export
    written before a close still says ``open``, so the filter errs toward *showing* a
    question, never toward hiding one.
    """
    closed = closed_ids(repo_root)
    items: list[DecisionItem] = []
    for issue_id in policy.session_issue_ids(repo_root, root_issue):
        if issue_id in closed:
            continue
        items += [i for i in items_by_id(repo_root, issue_id).values() if i.pending]
    return tuple(items)


def closed_ids(repo_root: Path) -> frozenset[str]:
    """Every bead id the committed export records as closed; empty when unreadable.

    Unreadable yields the empty set rather than raising, so a missing export degrades
    to the pre-basicly-jr0l.24 behaviour (report everything) instead of hiding the
    queue.
    """
    return frozenset(
        str(record["id"])
        for record in tracker.all_records(repo_root)
        if record.get("status") == "closed" and record.get("id")
    )


def settle_checkpoint(
    repo_root: Path, issue_id: str, name: str, *, by: str
) -> tuple[DecisionItem, ...]:
    """Answer the queue items asking for the *name* checkpoint on *issue_id*.

    A checkpoint approved by any path leaves the item behind it open otherwise: the
    supervisor queues the ask, and only ``loop answer`` ever cleared one, so a bead
    could ship and close with its own approval request still reading as pending
    (basicly-jr0l.24).

    Matched by kind and by the checkpoint name appearing in the question, rather than
    against a reconstructed question string. The wording lives at the enqueue site, and
    keying on an exact copy of it here would mean a reworded ask silently stopped
    clearing — the failure being fixed, reintroduced one refactor later. It also lets a
    legacy item, worded before this existed, still settle.

    Answering is what closes the item's ``[harness-wait]`` interval, and it closes it
    exactly once: :func:`answer` refuses an already-answered item, so only the pending
    ones below are touched. The checkpoint's *own* wait
    (:func:`policy.record_checkpoint_wait`) is a separate interval on a separate id —
    both are closed once, neither twice.
    """
    settled: list[DecisionItem] = []
    for item in items_by_id(repo_root, issue_id).values():
        if item.kind != "checkpoint" or not item.pending or name not in item.question:
            continue
        settled.append(
            answer(
                repo_root,
                item.decision_id,
                f"the {name} checkpoint was approved, so the queued ask is settled",
                by=by,
            )
        )
    return tuple(settled)


def has_pending(repo_root: Path, issue_id: str) -> bool:
    """True when *issue_id* has an unanswered item.

    The supervisor must not re-dispatch a lane that is waiting on a judgment
    (basicly-kjc5.7) — the run would only re-block on the same missing answer.
    """
    return any(item.pending for item in items_by_id(repo_root, issue_id).values())


def items_on(repo_root: Path, issue_id: str) -> tuple[DecisionItem, ...]:
    """Every item recorded on one bead, answered or not, in marker (oldest-first) order.

    The session-wide reads (:func:`pending`) walk the tree; this is the per-bead
    read a caller needs when it is already looking at one lane — e.g. folding the
    lane's answered questions into its next dispatch prompt.
    """
    return tuple(items_by_id(repo_root, issue_id).values())


def get(repo_root: Path, decision_id: str) -> DecisionItem | None:
    """The item recorded under *decision_id*, answered or not; None when absent."""
    issue_id, _ = split_decision_id(decision_id)
    return items_by_id(repo_root, issue_id).get(decision_id)


# --- Notify hook (design 7.3): consumer command per human-required item ------


def _notify(repo_root: Path, item: DecisionItem) -> None:
    """Fire ``[policy] notify_command`` for *item*; best-effort, never fatal.

    The configured argv gets the decision id and the question appended, so a
    one-line consumer script (desktop toast, Slack webhook) needs no parsing.
    No default — notification is opt-in per repo/machine.
    """
    argv = load_policy_config(repo_root).notify_command
    if not argv:
        return
    with contextlib.suppress(OSError, subprocess.SubprocessError):
        # `argv` is the consumer's own `[policy] notify_command`, not input crossing a
        # boundary; `item.question` — the only agent-influenced value — is one list
        # element under `shell=False`, so it can never be word-split into a command.
        subprocess.run(  # noqa: S603 — consumer-owned argv, list form, no shell
            [*argv, item.decision_id, item.question],
            check=False,
            capture_output=True,
            timeout=30,
        )


# --- Decider invocation (design 7.1): corpus-bounded authority ----------------


# Attribution for an item the engine itself disposes of because the fact it asked
# about stopped being actionable — not a judgment, so it is deliberately *not* a
# decider answer and never counts against the decider budget (basicly-jr0l.52).
# It carries no model authority: the engine may only retire its own moot questions,
# never answer one that still has a live subject.
ENGINE_BY = "engine"


def decider_answers_count(repo_root: Path, root_issue: str) -> int:
    """Delegated answers recorded so far this session (the runaway-loop meter)."""
    count = 0
    for issue_id in policy.session_issue_ids(repo_root, root_issue):
        for item in items_by_id(repo_root, issue_id).values():
            if item.answered_by and item.answered_by.startswith(DECIDER_BY_PREFIX):
                count += 1
    return count


def invoke_decider(  # noqa: PLR0911 — one return per distinct drop-to-human cause
    repo_root: Path,
    decision_id: str,
    root_issue: str,
    *,
    config: PolicyConfig | None = None,
) -> DecisionItem | DeciderVerdict:
    """Ask the decider agent to resolve *decision_id*; record only a real answer.

    Returns the answered :class:`DecisionItem` when the decider decided, or
    the abstaining :class:`DeciderVerdict` when the item stays with the human
    — because the decider abstained (fact not derivable from the corpus), its
    output did not parse, the session already spent its
    ``decider_max_decisions`` budget (the runaway-loop guard; D3's
    drop-to-human stance), or the selected runner family has no confinement
    overlay to bound it with (basicly-kjc5.16).
    """
    config = config or load_policy_config(repo_root)
    item = get(repo_root, decision_id)
    if item is None:
        raise ValueError(f"no decision {decision_id!r} recorded")
    if not item.pending:
        return item
    if decider_answers_count(repo_root, root_issue) >= config.decider_max_decisions:
        return DeciderVerdict(
            "",
            f"decider_max_decisions ({config.decider_max_decisions}) reached "
            "for this session; remaining decisions are human-only",
            0.0,
            abstain=True,
        )
    runner_config = load_runner_config(repo_root)
    selected = runner.select_runner(
        runner_config.specs, runner_config.decider or runner_config.default
    )
    # Confined at invocation (basicly-kjc5.16): the corpus bound and the answer cap
    # are contract text, and a decider holding a shell or write tool can simply
    # record its own answer around them. A family with no known overlay is not
    # dispatched at all — an unconfined decider is worse than a slower human.
    spec = runner.confine_for_decider(selected)
    if spec is None:
        return DeciderVerdict(
            "",
            f"runner {selected.name!r} has no known tool-confinement overlay, so the decider "
            "cannot be bounded to the intake corpus; this decision stays human-only",
            0.0,
            abstain=True,
        )
    # Bounded and metered like every other dispatch (basicly-kjc5.31): without the
    # timeout a hung decider hangs the pass forever, and without the run-record its
    # tokens never count against the session's D3 grant ceiling.
    #
    # Metered means passing `capture_usage` (basicly-gczc). Writing the run-record was
    # never enough on its own: unflagged, the record carried the chars/4 estimate,
    # and `policy.session_spend` counts an estimated dispatch as an *unmeterable*
    # one — which zeroes the remaining budget, and did halt the whole grant while spend
    # still gated (basicly-hnnmk9.1). The flag makes the number the adapter's own. It also
    # wraps the reply in a usage envelope, so the verdict is read back through
    # `runner.result_text` below rather than off raw stdout.
    prompt = decider_prompt(item, intake_corpus(repo_root, root_issue))
    # The decider's own reserved slot: it must be dispatchable even with every
    # lane slot busy, because those lanes are what wait on its answers.
    with runner.process_budget().slot(runner.DECIDER):
        result = runner.run(
            spec, prompt, repo_root, capture_usage=True, timeout=runner_config.runner_timeout
        )
    runner.record_dispatch(
        repo_root,
        item.issue_id,
        spec,
        result,
        prompt=prompt,
        phase=run_record.DECIDE_PHASE,
    )
    if result.timed_out or result.handoff or result.returncode != 0:
        # One outcome, three causes: nothing usable came back, so the item stays
        # with the human. Naming the timeout distinctly matters for triage — a
        # hung decider is an operational problem, not a missing CLI.
        why = (
            f"decider hit runner_timeout ({runner_config.runner_timeout:.0f}s)"
            if result.timed_out
            else "decider runner unavailable or failed"
        )
        return DeciderVerdict("", why, 0.0, abstain=True)
    verdict = parse_verdict(runner.result_text(spec, result.stdout))
    if verdict.abstain or not verdict.decision:
        return verdict
    # Re-check the cap inside the lock before recording. The check above ran before
    # a dispatch that takes minutes, during which concurrent lanes may have
    # recorded their own delegated answers — without this, N threads each pass a
    # stale check and the session overshoots decider_max_decisions (kjc5.17). The
    # dispatch itself stays outside the lock; only the count-and-record is atomic.
    with _QUEUE_LOCK:
        if decider_answers_count(repo_root, root_issue) >= config.decider_max_decisions:
            return DeciderVerdict(
                "",
                f"decider_max_decisions ({config.decider_max_decisions}) reached while "
                "this decision was being judged; it stays human-only",
                0.0,
                abstain=True,
            )
        return answer(
            repo_root,
            decision_id,
            verdict.decision,
            by=f"{DECIDER_BY_PREFIX}{spec.name}",
            rationale=verdict.rationale,
            confidence=verdict.confidence,
        )

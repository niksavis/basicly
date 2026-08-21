"""The derivations a board caller supplies because the producer may not make them.

`board_sections.units` states the rule: "both stay absent until a caller supplies them". A
second spelling of `loop_state.derive_phase` inside the producer is how two derivations come
to disagree, so every fact it refuses is gathered here, at the tier that may honestly derive
it. Split out of `board_cli` when two lanes landed on it in one day and the merge crossed the
size ratchet at 4108 of 4000 tokens - each branch green, the merge red (basicly-nwx4ku).

Every read fails to an absence, never a zero: an unfillable section is omitted and its panel
says the producer did not emit it, which is true.
"""

# comment-density-waiver: cohesion: 56.1% because the split moved the code and its reasons
# together, and every comment left is a measurement or an incident rather than narration -
# PHASE_LIMIT carries the 591 ms per record that sets it, `grant_spend` carries the
# 177970761/4000000 a lifetime figure once drew against a grant ceiling, and `questions`
# carries why a wait marker holds no prose. Architecture 34.1: the size and density ratchets
# are jointly unsatisfiable on a split, so this is the priced outcome, not an unmeasured one.
# The code half went the other way - `board_cli` left the headroom report entirely.

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

from . import (
    board_sections,
    board_serve,
    board_snapshot,
    checkout,
    decisions,
    loop_state,
    owned_store,
    policy,
    run_record,
    tracker_query,
)

# How many records get a loop phase, and the number is a cost rather than a taste.
# `loop_state.read_node_state` is the only route to `derive_phase` and it reads the whole log
# seven times per record - 591 ms over 20 records, measured 2026-08-21 - so all 234 active
# records is 138 s against a 171 ms build. The ranked ready front is the cut because it is the
# column a wall board is read for; outside it `phase` stays absent rather than guessed.
PHASE_LIMIT = 8

# What every fact-gathering read below treats as "no answer": no kit installed, an unreadable
# ledger, a report whose shape moved. Each costs its own key and never the page.
UNREADABLE = (owned_store.TrackerDivergenceError, OSError, ValueError, KeyError, TypeError)


def session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:
    """The supervisor lock's facts and the run's grant, or None where no lock names a root.

    Two lanes wrote this at once and the merge kept both halves. The lock reading lives in
    `board_serve` - the tier immediately below - because Mode A and Mode B both need it and a
    second reader is a second answer to "which pass is running". The grant is added here
    rather than there, because it is a fact about the run and not about the lock.

    None rather than a guessed root: the `session` section is then omitted and its panel says
    the producer did not emit it, which is true. A root invented here would be a claim about
    which pass is running, drawn on a wall.

    The grant rides along: `policy.active_grant` is one comment walk, 0.17 s measured. Nothing
    else here is that cheap, hence :func:`grant_spend`.
    """
    facts = board_serve.session_facts(repo_root)
    if facts is None:
        return None
    grant = active_grant(repo_root, facts.root_issue)
    return replace(
        facts,
        grant_level=grant.level if grant is not None else "",
        token_budget=grant.token_budget if grant is not None else None,
        spent_tokens=grant_spend(repo_root, facts.root_issue, grant),
    )


def active_grant(repo_root: Path, root_issue: str) -> policy.Grant | None:
    """The run's active grant, or None when there is none or the tracker will not answer."""
    try:
        return policy.active_grant(repo_root, root_issue)
    except UNREADABLE:
        return None


def grant_spend(repo_root: Path, root_issue: str, grant: policy.Grant | None) -> int | None:
    """Spend under *grant* - the only figure its `token_budget` bounds - or None.

    **The window is the whole point.** Publishing lifetime spend beside a grant's ceiling is how
    a display comes to draw 177970761/4000000 with nothing spent under that grant
    (basicly-e2mz.13); `policy.tokens_under_grant` is the subtraction that makes the pair
    comparable.

    Two guards, each omitting the key rather than reporting a zero: no grant is no window, and
    no run-record file means this checkout cannot see the spend at all - a fresh worktree has
    none - where `spend_status` answers 0 and renders as a session that spent nothing. Behind
    both sits `policy.session_issue_ids` at 13.1 s, so the walk runs only where it is worth it.
    """
    if grant is None or not run_record.load_run_records(repo_root):
        return None
    try:
        status = policy.spend_status(repo_root, root_issue, grant=grant)
    except UNREADABLE:
        return None
    return policy.tokens_under_grant(status.spent_tokens, grant)


def repo_facts(repo_root: Path) -> board_sections.RepoFacts | None:
    """Which checkout and which commit, from git, or None when git will not answer.

    Here rather than in the producer because `dirty` is `git status` and the producer spawns no
    subprocess, pinned by a spy in `tests/test_board_snapshot.py`. `--porcelain=v1 -b` answers
    the branch and the dirt at once, its header line carrying the branch.
    """
    try:
        state = checkout.git(["status", "--porcelain=v1", "-b"], cwd=repo_root, check=False)
        head = checkout.git(["rev-parse", "--short", "HEAD"], cwd=repo_root, check=False)
    except OSError:
        return None
    if state.returncode != 0:
        return None
    lines = state.stdout.splitlines()
    header = lines[0].removeprefix("## ") if lines else ""
    # A detached HEAD reports `## HEAD (no branch)`, which is not a branch name and is omitted
    # rather than published as one. The `...upstream` suffix is not this checkout's branch.
    branch = "" if header.startswith("HEAD (no branch)") else header.partition("...")[0]
    return board_sections.RepoFacts(
        branch=branch,
        head=head.stdout.strip() if head.returncode == 0 else "",
        dirty=any(line.strip() for line in lines[1:]),
    )


def readiness(repo_root: Path) -> board_sections.Readiness | None:
    """The tracker's own ready and blocked sets, or None when it will not answer.

    Read at this layer because `ready` is a derivation over a status vocabulary and the whole
    edge population that the kit's `queries` owns - the answer `basicly tracker ready` prints,
    reached through `tracker_query` so this module is not a second one on the store's seam.
    """
    try:
        return board_sections.Readiness(
            ready=frozenset(
                str(row["record"]) for row in tracker_query.ready_report(repo_root)["records"]
            ),
            blocked=frozenset(
                str(row["record"]) for row in tracker_query.blocked_report(repo_root)["records"]
            ),
        )
    except UNREADABLE:
        return None


def phases(repo_root: Path) -> dict[str, str]:
    """A loop phase for the front of the ready queue, keyed by record; empty on no answer.

    Bounded by :data:`PHASE_LIMIT`, and read through `loop_state.read_node_state` so
    `derive_phase` stays the one derivation - a phase folded out of the ledger alone diverges
    from the engine's for any unit owing validation, and renders identically.
    """
    found: dict[str, str] = {}
    try:
        front = tracker_query.ready_report(repo_root, PHASE_LIMIT)["records"]
        config = loop_state.load_policy_config(repo_root)
        for row in front:
            record = str(row["record"])
            found[record] = loop_state.read_node_state(repo_root, record, config).phase
    except UNREADABLE:
        return {}
    return found


def questions(repo_root: Path, document: dict[str, object]) -> dict[str, str]:
    """The wording behind each pending ask in *document*, keyed by wait id.

    **Read off the document's own asks, and the cost is why.** A request marker carries no prose
    - `policy.record_wait_request` writes an id, a kind and `requested` - so the wording lives
    only on the decision queue, which `decisions.pending` reaches through
    `policy.session_issue_ids` at 13.1 s. The asks already found name the records to ask about
    instead, so the read is one per pending ask and none when nothing is pending.

    Paired on the checkpoint name appearing in the question, `decisions.settle_checkpoint`'s own
    rule: the wording lives at the enqueue site, so keying on a reconstruction of it would stop
    pairing the moment an ask is reworded.
    """
    asks = document.get("asks")
    if not isinstance(asks, list):
        return {}
    found: dict[str, str] = {}
    for ask in asks:
        wait_id, issue = str(ask.get("wait_id", "")), str(ask.get("issue", ""))
        subject = str(ask.get("subject", ""))
        if not (wait_id and issue and subject):
            continue
        try:
            items = decisions.items_on(repo_root, issue)
        except UNREADABLE:
            continue
        for item in items:
            if item.pending and subject in item.question:
                found[wait_id] = item.question
    return found

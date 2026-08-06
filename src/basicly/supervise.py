"""Supervisor core: lock, session, recovery, and concurrent dispatch.

Factory design D1/7.2: one deterministic supervisor process per repo owns the
base checkout, the machine concurrency budget, and the single-writer usage
files — so supervisor-ness itself must be a singleton. Part 1 (basicly-kjc5.5)
built the lock, the session definition, and crash recovery. Part 2
(basicly-kjc5.6) adds the concurrent dispatch layer on those primitives:

- **Dispatch bundles are pure functions of ``br`` state at dispatch time**
  (D6): each lane's prompt is assembled from the issue record the moment its
  runner starts, folding in any ``[harness-info]`` found-info records other
  lanes published since the work was planned. Nothing is ever injected into a
  running lane.
- **Concurrency honors the worktree cap**: ready lanes fan out over a bounded
  thread pool, and the holder keeps heartbeating the singleton lock between
  completions so a long dispatch pass is never declared stale.
- **The usage meter** (D8) reads each run's final context occupancy from the
  adapter and, at ``[policy.sizing] context_ceiling`` of the runner's window,
  triggers the finalize protocol: the remainder becomes a follow-up bead — a
  new top-level package gated on the overrun lane's landing (design 7.6).

Outcome routing (green → merge-ready, block → decision queue) and standing
merge-queue integration are part 3 (kjc5.7); ``basicly loop supervise`` runs
one derivation + dispatch pass under the lock and reports the outcomes.

A lane the pass **held** (green and committed, but landed after another lane's
landing failed) is carried into the next pass as a landing-only outcome rather
than dispatched again (basicly-kjc5.18): its runner already finished and
committed, so a fresh implement-and-commit dispatch would pay for work that is
on the branch. The carry lapses the moment the lane's own work needs changing —
rework, a bounce, a retry — because that is when a dispatch is the right move.

Three rules, all from the design:

- **Lock** — ``.basicly/usage/supervisor.lock`` created with ``O_CREAT|O_EXCL``
  (atomic, portable), carrying PID + session id + root issue. Liveness is the
  file's **heartbeat mtime**, refreshed by the holder; a lock older than
  :data:`STALE_AFTER_S` is a crashed holder and is taken over atomically — no
  PID probing (avoids platform divergence and new dependencies).
- **Session** — one supervisor run bound to one root issue, identified by the
  session id in the lock file. Grant expiry (D3) and supervisor lifetime both
  reference this definition.
- **Recovery is derivation, not replay** — the supervisor keeps no side-state,
  so a restart rebuilds everything from ``br``: children of the root issue with
  a ``worktree:`` ``external_ref`` binding are re-adopted as in-flight lanes,
  cross-checked against the live worktree session records.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import Future, ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from . import (
    br,
    commit,
    decisions,
    decompose,
    loop,
    loop_state,
    merge,
    needs_input,
    policy,
    run_record,
    runner,
    worktree,
)
from .br import run_br as _run_br
from .br import try_run_br as _try_run_br
from .config import (
    AUTONOMY_LEVELS,
    SizingConfig,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)
from .redact import redact_secrets

LOCK_FILE = Path(".basicly/usage/supervisor.lock")

# Heartbeat cadence for the holder, and the staleness horizon for contenders.
# Fixed semantics, not config: 4 missed beats = a crashed holder (design 7.2).
HEARTBEAT_INTERVAL_S = 15.0
STALE_AFTER_S = 60.0


class LockHeldError(RuntimeError):
    """Another supervisor holds (or just took over) the singleton lock."""


class LockLostError(RuntimeError):
    """The holder's lock vanished — a contender declared it stale and took over."""


@dataclass(frozen=True)
class LockInfo:
    """The recorded holder of the supervisor lock, plus its heartbeat age."""

    pid: int | None
    session_id: str | None
    root_issue: str | None
    age_s: float


def new_session_id(root_issue: str) -> str:
    """A fresh session id: the root issue plus a short random suffix."""
    return f"{root_issue}:{secrets.token_hex(4)}"


def _now() -> float:
    """Wall-clock seconds; indirection so tests can pin the clock."""
    return time.time()


def read_holder(repo_root: Path) -> LockInfo | None:
    """The current lock holder and heartbeat age, or None when no lock exists.

    Best-effort on content: a corrupt payload still reports the heartbeat age
    (staleness is mtime-only by design), with the identity fields None.
    """
    path = repo_root / LOCK_FILE
    try:
        age = _now() - path.stat().st_mtime
    except OSError:
        return None
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        data = {}
    if not isinstance(data, dict):
        data = {}
    pid = data.get("pid")
    return LockInfo(
        pid=pid if isinstance(pid, int) else None,
        session_id=data.get("session_id") if isinstance(data.get("session_id"), str) else None,
        root_issue=data.get("root_issue") if isinstance(data.get("root_issue"), str) else None,
        age_s=age,
    )


def _create_lock(path: Path, payload: str) -> None:
    """Create the lock file atomically; FileExistsError when someone else won."""
    fd = os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
    with os.fdopen(fd, "w", encoding="utf-8") as handle:
        handle.write(payload)


def acquire(repo_root: Path, session_id: str, root_issue: str) -> Path:
    """Acquire the singleton supervisor lock; raise :class:`LockHeldError` otherwise.

    A fresh lock (heartbeat younger than :data:`STALE_AFTER_S`) refuses the
    contender with the holder's identity. A stale lock is taken over
    atomically: the contender renames it aside first — ``os.rename`` succeeds
    for exactly one contender; every loser gets ``FileNotFoundError`` — then
    re-creates it with ``O_CREAT|O_EXCL``, so two racing takeovers can never
    both believe they own the repo.
    """
    path = repo_root / LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    gitignore = path.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    payload = json.dumps(
        {"pid": os.getpid(), "session_id": session_id, "root_issue": root_issue},
        indent=2,
        sort_keys=True,
    )
    try:
        _create_lock(path, payload)
        return path
    except FileExistsError:
        pass

    holder = read_holder(repo_root)
    if holder is None:
        # The holder released between our failed create and this read: the
        # lock is free, not contested — try the plain create once more.
        try:
            _create_lock(path, payload)
            return path
        except FileExistsError as exc:
            raise LockHeldError("another supervisor acquired the freed lock first") from exc
    if holder.age_s < STALE_AFTER_S:
        raise LockHeldError(
            f"supervisor {holder.session_id or 'unknown'} (pid {holder.pid or '?'}) holds the "
            f"lock, heartbeat {holder.age_s:.0f}s old (stale after {STALE_AFTER_S:.0f}s)"
        )
    # Stale: steal it via the atomic rename. replace (not rename) so a
    # tombstone abandoned by a crashed same-pid contender never blocks a
    # takeover on Windows, where rename refuses an existing destination.
    tombstone = path.with_name(f"{path.name}.stale.{os.getpid()}")
    try:
        path.replace(tombstone)
    except OSError as exc:
        raise LockHeldError("another supervisor is taking over the stale lock") from exc
    tombstone.unlink(missing_ok=True)
    try:
        _create_lock(path, payload)
    except FileExistsError as exc:
        raise LockHeldError("another supervisor re-created the lock during takeover") from exc
    return path


def heartbeat(lock_path: Path, session_id: str) -> None:
    """Refresh the lock's liveness mtime; raise :class:`LockLostError` when not ours.

    Ownership is fenced by content, not file existence: after a takeover the
    path almost always holds the *successor's* lock (the rename-then-recreate
    window is microseconds), so a stalled-then-resumed holder would otherwise
    keep beating a lock it no longer owns — two live supervisors. A missing,
    unreadable, or foreign-session lock all mean the same thing: this holder
    was declared stale and must stop supervising immediately.
    """
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise LockLostError("supervisor lock vanished; a contender took over") from exc
    if not (isinstance(data, dict) and data.get("session_id") == session_id):
        raise LockLostError("supervisor lock now belongs to a successor session")
    os.utime(lock_path, None)


def release(lock_path: Path, session_id: str) -> None:
    """Remove the lock if this session still owns it; never delete a successor's.

    After a takeover the file belongs to the new holder, so ownership is
    re-checked by content before unlinking. Missing or unreadable locks are
    left alone — release is idempotent and never raises on a clean shutdown.
    Accepted residual race: a takeover completing entirely between the read
    and the unlink deletes the successor's lock; it requires the releasing
    holder to already be past the staleness horizon, and the successor's next
    heartbeat detects the loss and stands down (fail-safe, not fail-double).
    """
    try:
        data = json.loads(lock_path.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return
    if isinstance(data, dict) and data.get("session_id") == session_id:
        lock_path.unlink(missing_ok=True)


class HeartbeatThread(threading.Thread):
    """Background heartbeat so long phases never let the lock go stale.

    Dispatch waits beat inline, but routing lands lanes through full verify
    suites that easily exceed :data:`STALE_AFTER_S` — without a background
    beat a contender would take the lock over mid-merge, the exact
    two-supervisors state the lock exists to prevent (basicly-kjc5.7). A lost
    lock is captured, not raised (threads cannot signal the main loop
    directly); callers poll :meth:`check` between steps and pass it as the
    ``beat`` callback so a takeover stops the pass promptly.
    """

    def __init__(
        self, lock_path: Path, session_id: str, interval: float = HEARTBEAT_INTERVAL_S
    ) -> None:
        """Bind the beater to one lock file and session; daemonized by default."""
        super().__init__(name="supervisor-heartbeat", daemon=True)
        self._lock_path = lock_path
        self._session_id = session_id
        self._interval = interval
        self._stopped = threading.Event()
        self.lost: LockLostError | None = None

    def run(self) -> None:
        """Beat until stopped; capture (do not raise) a lost lock."""
        while not self._stopped.wait(self._interval):
            try:
                heartbeat(self._lock_path, self._session_id)
            except LockLostError as exc:
                self.lost = exc
                return

    def stop(self) -> None:
        """Stop beating (idempotent); the holder is releasing or has lost the lock."""
        self._stopped.set()

    def check(self) -> None:
        """Raise the captured :class:`LockLostError`, if the lock was taken over."""
        if self.lost is not None:
            raise self.lost


# --- Session state: derivation from br (recovery = re-reading) ---------------


@dataclass(frozen=True)
class AdoptedLane:
    """One in-flight lane re-adopted from its ``br`` worktree binding."""

    issue_id: str
    status: str
    binding: loop_state.WorktreeBinding
    # True when the worktree session record still exists on disk; a bound issue
    # whose worktree is gone needs a re-dispatch, not an adoption.
    live: bool


@dataclass(frozen=True)
class SessionState:
    """The supervisor's view of one session, derived purely from ``br``."""

    root_issue: str
    root_status: str
    children: tuple[tuple[str, str], ...]  # (issue_id, status)
    adopted: tuple[AdoptedLane, ...]

    @property
    def open_children(self) -> tuple[str, ...]:
        """Ids of the children this session may still size, fund and dispatch.

        The admitted statuses are named by
        :func:`loop_state.is_dispatchable` rather than excluded one at a time.
        A ``deferred`` child is not one of them, at either of the two sites that
        read this: it is left out of the band table, the open-child total and the
        ``cap x per-lane`` forecast, and it does not hold the session open below
        (basicly-toj6).
        """
        return tuple(cid for cid, status in self.children if loop_state.is_dispatchable(status))

    @property
    def done(self) -> bool:
        """True when the session's work is finished (root closed, or no open child).

        Fan-in reads the same rule as dispatch, so an epic whose only remaining
        child is one somebody deferred completes instead of waiting on it forever.
        """
        if self.root_status == "closed":
            return True
        return bool(self.children) and not self.open_children


def derive_session(repo_root: Path, root_issue: str) -> SessionState:
    """Rebuild the session's state from ``br`` — the whole crash-recovery story.

    The supervisor keeps no side-state, so this derivation is both cold start
    and restart: the root issue's parent-child dependents are the session's
    lanes, and any open child carrying a ``worktree:`` ``external_ref`` binding
    is re-adopted as in-flight, flagged ``live`` when its worktree session
    record still exists on disk. One ``br show`` per open child (matching the
    loop's per-issue reads); fine for a derivation pass, but the kjc5.6
    standing loop should not re-derive on every tick.
    """
    proc = _run_br(repo_root, ["show", root_issue, "--json"])
    data = json.loads(proc.stdout)
    record = data[0] if isinstance(data, list) else data
    if not isinstance(record, dict):
        raise RuntimeError(f"br show {root_issue} returned no issue record")

    children = tuple(
        (str(dep["id"]), str(dep.get("status", "")))
        for dep in record.get("dependents") or []
        if isinstance(dep, dict) and dep.get("dependency_type") == "parent-child" and "id" in dep
    )

    live_names = {session.name for session in worktree.list_sessions(repo_root)}
    adopted: list[AdoptedLane] = []
    candidates = [(root_issue, str(record.get("status", "")))]
    candidates += [(cid, status) for cid, status in children]
    for issue_id, status in candidates:
        if status == "closed":
            continue
        binding = _binding_of(repo_root, issue_id, record if issue_id == root_issue else None)
        if binding is None:
            continue
        adopted.append(
            AdoptedLane(
                issue_id=issue_id,
                status=status,
                binding=binding,
                live=binding.name in live_names,
            )
        )

    return SessionState(
        root_issue=root_issue,
        root_status=str(record.get("status", "")),
        children=children,
        adopted=tuple(adopted),
    )


def _show_issue(repo_root: Path, issue_id: str) -> dict | None:
    """The issue's ``br show`` record, or None on an unexpected payload shape."""
    proc = _run_br(repo_root, ["show", issue_id, "--json"])
    data = json.loads(proc.stdout)
    record = data[0] if isinstance(data, list) else data
    return record if isinstance(record, dict) else None


def _binding_of(
    repo_root: Path, issue_id: str, record: dict | None
) -> loop_state.WorktreeBinding | None:
    """The issue's worktree binding, reading ``br`` unless *record* is at hand."""
    if record is None:
        record = _show_issue(repo_root, issue_id)
        if record is None:
            return None
    return loop_state.parse_worktree_ref(record.get("external_ref"))


# --- Client attach: read-only observation (design 7.3 layer 3) ---------------


@dataclass(frozen=True)
class LaneView:
    """One in-flight lane as an attached client sees it.

    :class:`AdoptedLane` plus what that lane last *ran* — the supervisor itself
    never needs the run history to decide anything, but a client asking "is this
    lane working or wedged?" cannot answer from the ``br`` binding alone.
    """

    issue_id: str
    status: str
    worktree: str
    branch: str
    live: bool
    last_agent: str | None = None
    last_outcome: str | None = None
    last_run_at: str | None = None
    last_tokens: int | None = None


@dataclass(frozen=True)
class Observation:
    """A second session's read-only view of one supervisor session (design 7.3)."""

    root_issue: str
    root_status: str
    children_total: int
    children_open: int
    done: bool
    lanes: tuple[LaneView, ...]
    pending_decisions: tuple[decisions.DecisionItem, ...]
    # The recorded lock holder, or None when nobody is supervising this repo. A
    # holder past the staleness horizon is reported rather than hidden: "crashed
    # holder, takeover allowed" and "working" are exactly what a client attaches
    # to find out apart.
    holder: LockInfo | None = None
    holder_stale: bool = False
    # False when the holder is supervising a *different* root — the lock is a
    # repo singleton, so an attached client can be looking at an unsupervised
    # session while another one runs.
    holder_on_this_root: bool = False
    grant_level: str | None = None
    token_budget: int | None = None
    spent_tokens: int = 0
    # Where the session's wall clock actually went (basicly-kjc5.51). Waiting on a
    # human dominates it, and the run record measures only dispatch — so the two
    # are reported side by side and never added together.
    human_wait_s: int = 0
    delegated_wait_s: int = 0
    dispatch_s: float = 0.0

    @property
    def supervised(self) -> bool:
        """True when a live supervisor is bound to this session's root."""
        return self.holder is not None and self.holder_on_this_root and not self.holder_stale


def observe(repo_root: Path, root_issue: str) -> Observation:
    """Snapshot the session a client just attached to — a pure read (design 7.3).

    Layer 3's status half. It is the same derivation the supervisor runs on
    every tick (:func:`derive_session`), plus the facts a client cannot get
    from the tracker alone: who holds the lock and how fresh their heartbeat is,
    what each in-flight lane last ran, how much of the grant's token budget
    (D3) the session has spent, and where its wall clock went — human wait time
    reported apart from dispatch time (D11, basicly-kjc5.51).

    Takes no lock and writes nothing, so any number of clients may attach while
    the supervisor works — and attaching to an *unsupervised* root is a valid
    read, not an error: ``holder`` is then None.
    """
    state = derive_session(repo_root, root_issue)
    holder = read_holder(repo_root)
    grant = policy.active_grant(repo_root, root_issue)
    wait = policy.session_wait_summary(repo_root, root_issue)
    return Observation(
        root_issue=state.root_issue,
        root_status=state.root_status,
        children_total=len(state.children),
        children_open=len(state.open_children),
        done=state.done,
        lanes=tuple(_lane_view(repo_root, lane) for lane in state.adopted),
        pending_decisions=decisions.pending(repo_root, root_issue),
        holder=holder,
        holder_stale=holder is not None and holder.age_s >= STALE_AFTER_S,
        holder_on_this_root=holder is not None and holder.root_issue == root_issue,
        grant_level=grant.level if grant is not None else None,
        token_budget=grant.token_budget if grant is not None else None,
        spent_tokens=policy.session_spend(repo_root, root_issue).measured_tokens,
        human_wait_s=wait.human_wait_s,
        delegated_wait_s=wait.delegated_wait_s,
        dispatch_s=wait.dispatch_s,
    )


def _lane_view(repo_root: Path, lane: AdoptedLane) -> LaneView:
    """Widen one adopted lane with its most recent run-record, if it has one."""
    latest = run_record.latest_record(repo_root, lane.issue_id)
    return LaneView(
        issue_id=lane.issue_id,
        status=lane.status,
        worktree=lane.binding.name,
        branch=lane.binding.branch,
        live=lane.live,
        last_agent=latest.agent if latest is not None else None,
        last_outcome=latest.outcome if latest is not None else None,
        last_run_at=latest.timestamp if latest is not None else None,
        last_tokens=latest.tokens if latest is not None else None,
    )


# --- Found-info records: cross-lane discoveries via br (design 7.4, D6) ------


# Comment marker carrying a structured cross-lane discovery, the same durable,
# attributable pattern as policy's [harness-policy]. The payload after the
# marker is one JSON object: kind, summary, detail, affects.
INFO_MARKER = "[harness-info]"

# The record kinds the design names; `coupling` additionally implies a missed
# dependency edge (proposed by the outcome routing, kjc5.7).
FOUND_INFO_KINDS = ("coupling", "constraint", "decision", "fact")

# Bounds on what folds into a dispatch prompt: found-info is agent-authored, so
# one runaway record (or a flood of them) must not bloat — or steer — every
# later lane's context, eating the very budget the ceiling meter guards.
_MAX_INFO_SUMMARY = 200
_MAX_INFO_DETAIL = 500
_MAX_FOLDED_RECORDS = 20


@dataclass(frozen=True)
class FoundInfo:
    """One cross-lane discovery a lane published through the tracker."""

    kind: str
    summary: str
    detail: str = ""
    # Issue ids and/or scope globs the discovery is relevant to.
    affects: tuple[str, ...] = ()
    # The bead the record was found on (the discovering lane); stamped by the
    # parser — a record being written does not carry it.
    source: str = ""


def _folded_ref(info: FoundInfo) -> str:
    """A stable reference to one folded found-info record, for the run-record.

    ``FoundInfo`` carries no id of its own, so identify it by its source bead,
    its kind, and a digest of its summary. That is enough to locate the exact
    comment again when diffing why two attempts on one node saw different
    prompts (D9) — bundle assembly truncates to the newest few, so without this
    the difference is unexplainable.
    """
    digest = hashlib.sha256(info.summary.encode("utf-8")).hexdigest()[:8]
    return f"{info.source or '?'}#{info.kind}-{digest}"


def record_found_info(repo_root: Path, issue_id: str, info: FoundInfo) -> None:
    """Publish *info* as a marker comment on *issue_id* (its ``source`` is implied).

    Discoveries propagate through ``br``, never into a running lane's context
    (D6): the supervisor folds matching records into *future* dispatch bundles.
    ``br`` stamps author and timestamp on the comment itself.
    """
    if info.kind not in FOUND_INFO_KINDS:
        raise ValueError(
            f"unknown found-info kind {info.kind!r}; expected one of {FOUND_INFO_KINDS}"
        )
    payload = json.dumps(
        {
            "kind": info.kind,
            "summary": info.summary,
            "detail": info.detail,
            "affects": list(info.affects),
        },
        sort_keys=True,
    )
    _run_br(repo_root, ["comments", "add", issue_id, f"{INFO_MARKER} {payload}"])


def parse_found_info(text: str, source: str) -> FoundInfo | None:
    """Parse one comment into a :class:`FoundInfo`, or None when it is not one.

    Best-effort: a malformed payload (bad JSON, unknown kind, empty summary) is
    skipped, never raised — a garbled advisory record must not wedge dispatch.
    Summary and detail are truncated at parse time so an oversized record is
    bounded everywhere downstream, not just in prompts.
    """
    stripped = text.strip()
    if not stripped.startswith(INFO_MARKER):
        return None
    try:
        data = json.loads(stripped[len(INFO_MARKER) :].strip())
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict):
        return None
    kind = data.get("kind")
    summary = data.get("summary")
    if kind not in FOUND_INFO_KINDS or not isinstance(summary, str) or not summary.strip():
        return None
    detail = data.get("detail")
    raw_affects = data.get("affects")
    affects = (
        tuple(a.strip() for a in raw_affects if isinstance(a, str) and a.strip())
        if isinstance(raw_affects, list)
        else ()
    )
    return FoundInfo(
        kind=kind,
        summary=summary.strip()[:_MAX_INFO_SUMMARY],
        detail=detail.strip()[:_MAX_INFO_DETAIL] if isinstance(detail, str) else "",
        affects=affects,
        source=source,
    )


def found_info_records(repo_root: Path, issue_ids: Iterable[str]) -> tuple[FoundInfo, ...]:
    """All found-info records published on *issue_ids*, in comment order."""
    records: list[FoundInfo] = []
    for issue_id in issue_ids:
        proc = _run_br(repo_root, ["comments", "list", issue_id, "--json"])
        try:
            comments = json.loads(proc.stdout)
        except json.JSONDecodeError:
            continue
        if not isinstance(comments, list):
            continue
        for comment in comments:
            if not isinstance(comment, dict):
                continue
            info = parse_found_info(str(comment.get("text", "")), source=issue_id)
            if info is not None:
                records.append(info)
    return tuple(records)


# --- Dispatch bundles: pure functions of br state at dispatch time (D6) ------


@dataclass(frozen=True)
class DispatchBundle:
    """One lane's dispatch prompt, assembled purely from ``br`` at dispatch time."""

    issue_id: str
    prompt: str
    folded: tuple[FoundInfo, ...]
    # Answered decisions folded into the prompt, newest last. Without these an
    # answer reaches nobody: the lane that blocked on the question re-dispatches
    # with the same prompt it had before (basicly-kjc5.40).
    answers: tuple[decisions.DecisionItem, ...] = ()


def build_bundle(
    repo_root: Path, issue_id: str, *, known_ids: frozenset[str] = frozenset()
) -> DispatchBundle:
    """Assemble *issue_id*'s dispatch bundle from ``br`` state right now.

    The base prompt is the loop's agent-neutral dispatch prompt; found-info
    records published on the session's beads (*known_ids*) are folded in when
    they affect this lane — named by issue id, or by a scope glob overlapping
    the lane's declared ``## Scope``. Because assembly happens at dispatch time,
    a record published while earlier lanes ran is naturally visible to every
    later dispatch, and never to one already in flight (D6).
    """
    record = _show_issue(repo_root, issue_id) or {}
    scope = decompose.parse_scope_section(str(record.get("description") or ""))
    sources = sorted({issue_id, *known_ids})
    records = found_info_records(repo_root, sources)
    matching = [r for r in records if _info_matches(r, issue_id, scope, known_ids)]
    # Newest-last comment order; under the cap, keep the most recent records —
    # they reflect the latest graph and landed work.
    folded = tuple(matching[-_MAX_FOLDED_RECORDS:])
    prompt = loop.dispatch_prompt(issue_id)
    if folded:
        lines = []
        for info in folded:
            line = f"- [{info.kind}] {info.summary}"
            if info.detail:
                line += f" — {info.detail}"
            lines.append(line + f" (recorded on {info.source})")
        prompt += (
            "\n\nCross-lane findings recorded since this work was planned; "
            "fold them into your approach:\n" + "\n".join(lines)
        )
    answers = answered_decisions(repo_root, issue_id)
    if answers:
        prompt += (
            "\n\nQuestions this work already blocked on, and the answers on "
            "record — treat them as decided, do not re-ask:\n"
            + "\n".join(
                f"- {item.question} → {item.answer} (answered by {item.answered_by})"
                for item in answers
            )
        )
    return DispatchBundle(issue_id=issue_id, prompt=prompt, folded=folded, answers=answers)


def answered_decisions(repo_root: Path, issue_id: str) -> tuple[decisions.DecisionItem, ...]:
    """The lane's own answered queue items, newest last and bounded like found-info.

    Only the lane's own items: a sibling lane's answer is that lane's context,
    and cross-lane facts travel as ``[harness-info]`` records by design (7.4).
    """
    items = [item for item in decisions.items_on(repo_root, issue_id) if not item.pending]
    return tuple(items[-_MAX_FOLDED_RECORDS:])


def _info_matches(
    info: FoundInfo, issue_id: str, scope: tuple[str, ...], known_ids: frozenset[str]
) -> bool:
    """True when *info* affects this lane: by issue id, or by scope-glob overlap.

    An ``affects`` entry naming a *different* session bead is an id reference,
    not a glob — it must not be glob-tested against this lane's scope, where a
    broad pattern like ``**`` would false-fold every record everywhere.
    """
    for entry in info.affects:
        if entry == issue_id:
            return True
        if entry == info.source or entry in known_ids:
            continue
        if scope and decompose.scopes_overlap((entry,), scope):
            return True
    return False


def coupled_beads(
    repo_root: Path, info: FoundInfo, candidates: Iterable[str], known_ids: frozenset[str]
) -> tuple[str, ...]:
    """The *candidates* a coupling record affects, by the same test folding uses.

    Deliberately :func:`_info_matches`, not a second implementation: the bead that
    earns an edge from a record must be exactly the bead that gets the record
    folded into its next prompt, or the graph and the prompts would disagree
    about what a discovery means. The record's own source is excluded — a lane
    cannot be coupled to itself.
    """
    found: list[str] = []
    for bead in sorted(candidates):
        if bead == info.source:
            continue
        read = decompose.bead_class_and_scope(repo_root, bead)
        scope = read[1] if read is not None else ()
        if _info_matches(info, bead, scope, known_ids):
            found.append(bead)
    return tuple(found)


def propose_coupling_edges(
    repo_root: Path, session: SessionState
) -> tuple[tuple[str, str, str], ...]:
    """Turn ``kind=coupling`` discoveries into dependency edges (D6, design 7.4).

    D6 has the graph learn a coupling from a *discovery*, not only from a merge
    collision: a lane that finds out two packages are coupled teaches the graph
    immediately, so the next decomposition serializes them instead of declaring
    them parallel-safe again. Reading the records was already built (they fold
    into later dispatch bundles); this is the write half.

    Which edge depends on whether the affected bead has started, and that
    distinction is the whole lesson of basicly-grrb:

    - **Not started** — a ``blocks`` edge, which gates. Nothing is lost by making
      it wait and a collision is prevented outright, which is the point of
      learning the coupling before either lane runs.
    - **In flight** — a non-gating :func:`merge.record_coupling` edge instead.
      That lane has committed work; gating it would drop it out of
      :func:`ready_lanes` and strand that work behind a human, which is exactly
      the defect grrb fixed on the bounce path. It still learns the discovery the
      way D6 intends — the record folds into its next prompt.

    Runs once per pass from the supervisor loop rather than inside
    :func:`build_bundle`, which is called concurrently from the dispatch threads:
    writing edges there would mean concurrent ``br`` writes and a landing order
    that depended on thread scheduling.

    Idempotent per edge — an existing dependency between the two beads is left
    alone, whatever its type. ``br`` refuses a duplicate rather than changing its
    type, so an edge first recorded while a lane was in flight keeps its
    non-gating type afterwards; that is the safe direction to be wrong in.
    Returns the ``(bead, coupled_to, dep_type)`` triples recorded by this call.
    """
    open_children = frozenset(session.open_children)
    known = frozenset({session.root_issue, *(cid for cid, _ in session.children)})
    in_flight = {lane.issue_id for lane in session.adopted if lane.live}
    recorded: list[tuple[str, str, str]] = []
    for info in found_info_records(repo_root, sorted(known)):
        if info.kind != "coupling":
            continue
        for bead in coupled_beads(repo_root, info, open_children, known):
            if _already_coupled(repo_root, bead, info.source):
                continue
            if bead in in_flight:
                merge.record_coupling(repo_root, bead, info.source)
                recorded.append((bead, info.source, merge.COUPLING_DEP_TYPE))
            else:
                # Best-effort like every other coupling write: a cycle br refuses
                # must not end the pass.
                _try_run_br(repo_root, ["dep", "add", bead, info.source, "-t", "blocks"])
                recorded.append((bead, info.source, "blocks"))
    return tuple(recorded)


def _already_coupled(repo_root: Path, bead: str, coupled_to: str) -> bool:
    """True when *bead* already carries any dependency on *coupled_to*.

    Any type counts. ``br`` will refuse a second edge between the same pair
    anyway, so re-issuing one would only add a failed write per pass — and a
    ``parent-child`` edge already expresses the ordering a coupling would.
    Unreadable tracker reads as "already there": skipping an edge is recoverable
    next pass, while a spurious gating edge holds a lane.
    """
    try:
        record = _show_issue(repo_root, bead)
    except RuntimeError, OSError, ValueError:
        return True
    if record is None:
        return True
    for dep in record.get("dependencies") or []:
        if not isinstance(dep, dict):
            continue
        if (dep.get("depends_on_id") or dep.get("id")) == coupled_to:
            return True
    return False


# --- Usage meter: context ceiling + finalize protocol (D8, design 7.6) -------


# Comment marker recording that a lane's run crossed the context ceiling and
# which follow-up bead carries the remainder — the idempotence guard against a
# re-dispatched overrun spinning duplicate follow-ups.
OVERRUN_MARKER = "[harness-overrun]"


def ceiling_tokens(spec: runner.RunnerSpec, sizing: SizingConfig) -> int:
    """The finalize trigger for *spec*, in tokens of final context occupancy."""
    return int(spec.context_window * sizing.context_ceiling)


def existing_followup(repo_root: Path, issue_id: str) -> str | None:
    """The follow-up bead already spun for *issue_id*'s overrun, or None."""
    proc = _run_br(repo_root, ["comments", "list", issue_id, "--json"])
    try:
        comments = json.loads(proc.stdout)
    except json.JSONDecodeError:
        return None
    if not isinstance(comments, list):
        return None
    for comment in comments:
        if not isinstance(comment, dict):
            continue
        followup = _overrun_followup_id(str(comment.get("text", "")))
        if followup is not None:
            return followup
    return None


def _overrun_followup_id(text: str) -> str | None:
    """The ``followup=<id>`` recorded on an overrun marker comment, or None."""
    stripped = text.strip()
    first_line = stripped.splitlines()[0] if stripped else ""
    if not first_line.startswith(OVERRUN_MARKER + " "):
        return None
    for token in first_line.split()[1:]:
        if token.startswith("followup=") and len(token) > len("followup="):
            return token[len("followup=") :]
    return None


def finalize_followup(
    repo_root: Path, root_issue: str, issue_id: str, *, occupancy: int, ceiling: int
) -> str:
    """Spin the remainder of an overrun lane into a follow-up bead (design 7.6).

    A package-level overrun's remainder becomes a **new top-level package**: a
    sibling lane under the session root, gated by a ``blocks`` edge on the
    overrun bead so it dispatches only after the partial work lands — fresh
    worktree, merge-queue semantics preserved, flatten-don't-deepen (D7). The
    original acceptance criteria and scope are carried over; the follow-up's
    fresh dispatch reads the landed partial work through ``br`` and the tree,
    so nothing is lost. Idempotent via the overrun marker: a re-metered overrun
    returns the already-created follow-up.
    """
    existing = existing_followup(repo_root, issue_id)
    if existing is not None:
        return existing
    record = _show_issue(repo_root, issue_id) or {}
    title = f"Follow-up: {record.get('title') or issue_id} (context-ceiling overrun)"
    acceptance = str(record.get("acceptance_criteria") or "").strip()
    if not acceptance:
        acceptance = f"- Complete the remaining acceptance criteria of {issue_id}"
    scope = decompose.parse_scope_section(str(record.get("description") or ""))
    scope_lines = "\n".join(f"- `{glob}`" for glob in scope)
    if not scope_lines:
        scope_lines = f"- (inherits the declared scope of {issue_id})"
    issue_type = record.get("issue_type")
    if issue_type not in ("bug", "chore", "task"):
        issue_type = "task"
    # A follow-up is a new top-level package driven through its own loop, so it
    # meets its own DoR gate. Compose the body from the required-section set for
    # the type it inherits rather than the task set: a hand-written body here
    # dropped a bug follow-up's ``## Steps to Reproduce`` and the gate then
    # refused a bead the engine itself had created (basicly-kjc5.44).
    body = policy.compose_body(
        str(issue_type),
        {"## Acceptance Criteria": acceptance, "## Scope": scope_lines},
        preamble=(
            f"Continues {issue_id}: its run crossed the context ceiling "
            f"({occupancy} >= {ceiling} tokens), so the lane finalized early (factory design "
            "D8/7.6). Check which acceptance criteria the partial landing already satisfied "
            "before redoing work."
        ),
    )
    # Assembled in order rather than spliced by index: the previous form
    # inserted the parent at position 3, which is the *value* of ``-t``, so a
    # child lane's follow-up went out as ``-t --parent <root> <type>`` and br
    # rejected it for a missing type (basicly-jr0l.11). Appending each flag with
    # its value keeps the pairing local and removes the index arithmetic that
    # made the two separable at all.
    create_args = ["create", title, "-t", str(issue_type)]
    # The follow-up inherits the *classification* of the work, not just the work.
    # ``br create`` defaults an omitted priority to 2, so a P0 lane's remainder
    # used to come back as P2 and the scheduler — which ranks by priority — put
    # it behind every routine bead in the ready set. Labels matter for the same
    # reason in reverse: phase membership is a label rather than a re-parenting,
    # so an unlabelled follow-up is silently absent from ``br list --label``
    # and from any planning pass built on one (basicly-jr0l.25). Deliberately
    # *not* inherited: assignee, estimate, due date — the remainder is a
    # different size than the original, and a carried-over estimate is worse
    # than none.
    priority = record.get("priority")
    if priority is not None:
        create_args += ["-p", str(priority)]
    labels = [str(label) for label in (record.get("labels") or []) if str(label).strip()]
    if labels:
        create_args += ["-l", ",".join(labels)]
    if root_issue != issue_id:
        create_args += ["--parent", root_issue]
    create_args += ["-d", body, "--json"]
    proc = _run_br(repo_root, create_args)
    followup_id = str(json.loads(proc.stdout)["id"])
    _run_br(repo_root, ["dep", "add", followup_id, issue_id, "-t", "blocks"])
    _run_br(
        repo_root,
        [
            "comments",
            "add",
            issue_id,
            f"{OVERRUN_MARKER} followup={followup_id} occupancy={occupancy} ceiling={ceiling}",
        ],
    )
    return followup_id


@dataclass(frozen=True)
class CeilingVerdict:
    """What the context meter said about one finished dispatch."""

    occupancy: int | None
    ceiling: int
    overrun: bool
    followup_id: str | None


def meter_context_ceiling(  # noqa: PLR0913 — one parameter per metering input
    repo_root: Path,
    root_issue: str,
    issue_id: str,
    spec: runner.RunnerSpec,
    result: runner.RunResult,
    sizing: SizingConfig,
    *,
    landed: bool,
) -> CeilingVerdict:
    """Meter a finished dispatch against *spec*'s ceiling, finalizing if it crossed.

    The one metering site both write paths reach (basicly-7kxq). This used to be
    inline in the supervised lane and nowhere else, so the single-track path
    dispatched unmetered: basicly-23ep was driven through ``loop run``, recorded
    403051 tokens against a derived trigger of 120000, and was neither finalized
    early nor given a follow-up — the same work, at the same size, truncated under
    ``supervise`` and completed under ``loop run``. A second copy in ``loop`` is how
    the two paths would come to disagree again, so the supervisor calls this rather
    than keeping its own (the same reuse ``loop._dispatch_refused`` makes of
    :func:`admit_working_set`, for the same reason).

    *landed* is the caller's answer to "did this run leave a coherent partial
    landing?". A run that failed or stopped on a missing fact lands nothing, gets
    re-dispatched, and must not pin a premature remainder bead through the
    idempotence marker (design 7.6).
    """
    occupancy = runner.context_occupancy(spec, result)
    ceiling = ceiling_tokens(spec, sizing)
    overrun = occupancy is not None and occupancy >= ceiling
    followup_id = (
        finalize_followup(
            repo_root, root_issue, issue_id, occupancy=occupancy or 0, ceiling=ceiling
        )
        if overrun and landed
        else None
    )
    return CeilingVerdict(
        occupancy=occupancy, ceiling=ceiling, overrun=overrun, followup_id=followup_id
    )


# --- The working-set band at dispatch (D8 at the dispatch, basicly-jr0l.16) ---
#
# The sizing governor refuses an out-of-band *plan* at decompose, and nothing used
# to check the band again when a lane was dispatched — so the band bound only work
# that arrived through decompose. A supervised pass over pre-existing leaf beads,
# which is exactly what this module does over a ready set, started whatever the
# scheduler ranked first at any size. Re-measured on this repo's own ready set with
# the band at 8000..64000, the top-ranked lane estimates 108605 working-set tokens —
# 70% over the ceiling a plan would have been refused for — and three of its
# siblings are over it too. So the check belongs here, before anything spawns,
# exactly where ``runner.run`` refuses a dispatch whose model tier resolves to
# nothing (basicly-kjc5.59): cost is bounded by sizing the work, never by killing a
# working agent.
#
# The two ends of the band deliberately earn different severities:
#
# - **Above the ceiling the dispatch is refused.** The run would overflow the very
#   window it was sized against, so the tokens buy a partial answer at best. The
#   remedy — split the package — is a decompose action no engine can take, so the
#   lane is held by a *pending* queue item until a human takes it.
# - **Below the floor the dispatch is escalated and then proceeds.** An under-size
#   lane succeeds; it merely wastes the per-lane overhead that merging it with a
#   sibling would have saved. Holding it would strand deliverable work behind a
#   human answer over an economic inefficiency, and a ready set of small beads
#   would wedge a whole supervised run — the expensive failure in the cheap
#   direction.
#
# A lane whose scope cannot be read at all is **admitted**. That is not the same
# indeterminate answer as an estimate that lands outside the band: 60 of this
# repo's 87 open beads carry no ``## Scope`` section — including basicly-jr0l.16
# itself — so failing closed on a missing estimate would turn a sizing governor
# into a ban on hand-filed work, strictly worse than the gap it closes. The model
# precedent refuses because a *declared* tier resolved to nothing; an absent scope
# declares nothing to contradict.
#
# It is admitted **visibly**, though, and that is basicly-jr0l.60. Admitting was only
# half a decision: nothing distinguished a lane the band had checked and passed from
# one it had never looked at, in the admission, the queue or the pass output. Measured
# on the 2026-08-01 four-lane proof run, every dispatched lane was unsizeable, so the
# band checked *nothing* and reported exactly what it reports when everything fits —
# and all four then overran the context ceiling they were never compared against. So:
#
# * The **undeclared** case (the bead read fine and declares no scope) is a fact about
#   the bead, and it is escalated: recorded on the lane and named in the pass output,
#   then retired by the engine so the lane still dispatches. Same disposal as the
#   under-floor advisory, for the same reason — holding the majority of a real tracker
#   behind a human answer is the ban failing closed would have been. It is charged to
#   the engine rather than notified for the same reason: a notification per hand-filed
#   lane is a ban by noise.
# * The **unreadable** case (the read itself failed) stays genuinely indeterminate and
#   is not escalated. A transient br failure is not a finding about the package, and
#   queuing one would put a tracker hiccup in the audit trail as a sizing defect.


# The queue question an out-of-band lane asks, named once because
# :func:`decisions.enqueue` keys items by (issue, kind, question) — a second copy of
# this string would leak a pending item that nothing can find (basicly-jr0l.52).
# The numbers stay in the *detail*, which is not part of the id, so re-deriving the
# same pass finds the item it already queued instead of a fresh generation of it.
# Every remedy it names changes the check's own inputs, deliberately: this is a
# deterministic gate, so an answer releases the lane but never overrides the
# arithmetic — a lane answered without a re-scope refuses again on its next pass.
SIZING_QUESTION = "working set is outside the configured band: re-scope it or widen the band?"

# The queue question a never-checked lane asks — a distinct string, so an unsized lane
# and an out-of-band one are separate items rather than two generations of one, and an
# operator reading the queue can tell "too big" from "never measured".
UNSIZED_QUESTION = "working set was never checked: declare this package's scope?"

# How the engine retires each advisory it does not hold the lane for. The wording is
# the disposal reasoning, recorded where the decision is (D11).
_UNDERSIZE_DISPOSAL = (
    "dispatched anyway: under-cutting the floor wastes per-lane overhead, "
    "but the package is deliverable and holding it would strand it"
)
_UNSIZED_DISPOSAL = (
    "dispatched anyway: an undeclared scope is the normal state of a hand-filed bead, "
    "so holding it would ban hand-filed work rather than size it — recorded so this "
    "dispatch is not mistaken for one the band checked"
)


@dataclass(frozen=True)
class WorkingSetAdmission:
    """Whether a lane's estimated working set admits a dispatch (D8 at dispatch).

    *violation* is :func:`policy.check_working_set`'s own guidance message, so the
    band rule and its wording stay in one place; *refused* only classifies which end
    of the band was crossed, which is what decides severity.
    """

    issue_id: str
    # None when the bead declares no ``## Scope`` or could not be read at all.
    sizing: decompose.DispatchSizing | None
    violation: str | None
    refused: bool
    # Which absence left this lane unsized: ``decompose.SCOPE_UNDECLARED`` or
    # ``decompose.SCOPE_UNREADABLE``. Empty when *sizing* is there — so
    # :attr:`checked` reads off the estimate, not off this.
    absence: str = ""

    def record_inputs(self, repo_root: Path) -> dict[str, object]:
        """The dispatch record's sizing keywords; empty when nothing was estimable.

        Takes *repo_root* because one of those keywords is the forecast **spend**,
        which is resolved from this repo's calibration (basicly-tcmy.34).
        """
        return {} if self.sizing is None else self.sizing.record_inputs(repo_root)

    @property
    def checked(self) -> bool:
        """True when a real estimate was actually compared against the band.

        The distinction the surface used to lack (basicly-jr0l.60): ``violation is
        None`` answers "nothing was wrong", which is also what a lane nobody measured
        looks like.
        """
        return self.sizing is not None


def admit_working_set(repo_root: Path, issue_id: str, sizing: SizingConfig) -> WorkingSetAdmission:
    """Estimate *issue_id*'s working set and judge it against the band (pure read).

    Reuses :func:`decompose.resolve_dispatch_sizing` — the same estimator whose
    forecast the dispatch record already carries (basicly-jr0l.34) — so the number
    that gates a dispatch and the number recorded beside its actual cannot disagree.
    That also means no new tracker read: this *is* the read the dispatch already made,
    moved ahead of the bundle.

    Never raises. Neither absence refuses, on the reasoning in this section's header,
    but they are not the same admission: an undeclared scope carries the never-checked
    notice that :func:`escalate_working_set` records, while a failed read carries
    nothing, because it is a fact about the tracker rather than about the package.
    """
    lookup = decompose.SizingLookup(None, decompose.SCOPE_UNREADABLE)
    with contextlib.suppress(RuntimeError, ValueError, OSError):
        lookup = decompose.resolve_dispatch_sizing(repo_root, issue_id)
    resolved = lookup.sizing
    if resolved is None:
        unchecked = (
            policy.unchecked_working_set(issue_id, sizing)
            if lookup.absence == decompose.SCOPE_UNDECLARED
            else None
        )
        return WorkingSetAdmission(issue_id, None, unchecked, refused=False, absence=lookup.absence)
    estimate = resolved.estimate
    violation = policy.check_working_set(issue_id, estimate.total, estimate.scope_tokens, sizing)
    return WorkingSetAdmission(
        issue_id,
        resolved,
        violation,
        # `check_working_set` still decides *whether* the band was crossed; this
        # comparison only says which end, because only the ceiling refuses.
        refused=violation is not None and estimate.total > sizing.working_set_max,
    )


def escalate_working_set(
    repo_root: Path, admission: WorkingSetAdmission
) -> decisions.DecisionItem | None:
    """Queue an out-of-band or never-checked lane's sizing verdict; None when it fits.

    A refusal stays **pending**, and that is what holds the lane: ``ready_lanes``
    drops a lane with an unanswered item, so the package is not dispatched again
    until someone re-scopes it. Every other advisory — under the floor, or never
    checked at all because the bead declares no scope — is retired by the engine in
    the same breath: recorded for the audit trail, charged to the delegated column
    rather than a human's wait (D11), and out of ``has_pending`` — the same disposal
    :func:`resolve_stall_flag` makes of its own moot question.

    None also for a lane whose read failed: ``admit_working_set`` leaves that
    violation None precisely so a tracker hiccup is not filed as a sizing finding.
    """
    if admission.violation is None:
        return None
    unsized = admission.absence == decompose.SCOPE_UNDECLARED
    item = decisions.enqueue(
        repo_root,
        admission.issue_id,
        "escalation",
        UNSIZED_QUESTION if unsized else SIZING_QUESTION,
        admission.violation,
        human_required=admission.refused,
    )
    if not admission.refused:
        decisions.answer(
            repo_root,
            item.decision_id,
            _UNSIZED_DISPOSAL if unsized else _UNDERSIZE_DISPOSAL,
            by=decisions.ENGINE_BY,
        )
    return item


def band_coverage(working_sets: tuple[WorkingSetAdmission, ...]) -> str:
    """Which of this pass's lanes the band actually measured, and which it could not.

    Reported on every pass for the same reason :attr:`PassSpendAdmission.coverage` is:
    the failure this closes was silent. A pass of lanes the band never looked at
    printed nothing at all, so it was indistinguishable at the surface from a pass
    where every estimate fitted — and on the run that measured it, all four lanes were
    the former and all four overran (basicly-jr0l.60).
    """
    if not working_sets:
        return "no lanes to check"
    by_absence: dict[str, list[str]] = {}
    checked: list[str] = []
    for item in working_sets:
        if item.checked:
            checked.append(item.issue_id)
        else:
            by_absence.setdefault(item.absence, []).append(item.issue_id)
    parts = []
    if checked:
        parts.append(f"checked: {', '.join(checked)}")
    for absence, ids in sorted(by_absence.items()):
        parts.append(f"NEVER CHECKED ({absence}): {', '.join(ids)}")
    return "; ".join(parts)


def band_report(working_sets: tuple[WorkingSetAdmission, ...]) -> tuple[str, ...]:
    """One line per candidate: its working-set estimate and what the band would do.

    :func:`band_coverage` answers "did the band look?" in a single line, which is the
    right shape *during* a pass. Before one, the operator is deciding whether to mint a
    budget at all, and that decision needs the per-lane numbers — an aggregate forecast
    built from the unsizeable-lane assumption reads exactly like a measurement of lanes
    nobody measured (basicly-prnm, the same silent shape as basicly-jr0l.60).

    Ordered largest estimate first, then the unsized: the big lanes decide the budget,
    and the absent ones are the authoring fix that changes the budget most.
    """
    sized = sorted(
        (w for w in working_sets if w.sizing is not None),
        key=lambda w: w.sizing.estimate.total if w.sizing else 0,
        reverse=True,
    )
    lines = [
        f"  {w.issue_id:<22} {w.sizing.estimate.total:>9} tok  {_band_verdict(w)}"
        for w in sized
        if w.sizing is not None
    ]
    # Named, never folded into a count: an undeclared scope is an authoring fix that
    # takes a minute, and it is the largest single lever on what a pass costs.
    lines += [
        f"  {w.issue_id:<22} {'unsized':>9}      no scope the estimator can read"
        for w in working_sets
        if w.sizing is None
    ]
    return tuple(lines)


def _band_verdict(admission: WorkingSetAdmission) -> str:
    """What the band would do with one sized lane, worded for the pre-run table.

    The floor is deliberately skipped when a scope matches nothing on disk, so a
    greenfield package is not refused for having nothing to read yet
    (:func:`policy.check_working_set`). That leaves a *broken* glob indistinguishable
    from a greenfield one at the surface: both estimate to bare overhead and both read
    as a comfortably small "in band" lane. Measured on this repo's own tracker, four
    candidates sat at exactly the overhead figure. Say it instead, because the fix
    differs — one needs a corrected path, the other needs nothing (the gate that
    refuses an empty glob outright is basicly-a3ab.3).
    """
    if admission.refused:
        return "REFUSED - too large, split it"
    if admission.violation is not None:
        # Only the ceiling refuses (see :func:`admit_working_set`), so a lane under the
        # floor still dispatches while carrying the band's advice. Printing a bare
        # "in band" for it would report the opposite of what the band actually said.
        return "under the floor - dispatches, but merge it with a sibling"
    if admission.sizing is not None and admission.sizing.estimate.scope_tokens == 0:
        return "in band, but its scope matched no file"
    return "in band"


# --- Contention on a path no bead declares (basicly-o8p0) --------------------
#
# The one collision knowable before any lane starts, and the one the band table
# cannot see: a path this repo's conventions have EVERY lane append its own entry to
# appears in no bead's `## Scope`, so the disjoint-scope check that admits the pass is
# reading an incomplete list. Measured on the basicly-u6jq.1 proof run: three lanes,
# provably disjoint scopes, `VERDICT: ready`, and the third lane bounced twice on a
# `CHANGELOG.md` rebase conflict and spent its whole rework budget getting there.
#
# Reported here rather than only serialized at decompose, because the lanes that
# collided were hand-filed siblings that no plan ever grouped — nothing in
# `decompose` runs for them, and preflight is the only surface that sees the set.


def append_only_report(
    repo_root: Path, lanes: tuple[str, ...], paths: tuple[str, ...]
) -> tuple[str, ...]:
    """Whether this pass's lanes will contend on a configured append-only path.

    First line is coverage — which paths were checked, or that none is declared —
    for the reason :func:`band_coverage` exists: a check that prints nothing when it
    finds nothing is indistinguishable from a check that never ran, and this one is
    inert until a consumer lists a path. Then one line per path two or more lanes
    will each append to without declaring it.

    A lane that *declares* the path in its own ``## Scope`` is left out of the count:
    it has said out loud that it writes the file, so the scope-collision gate
    (``loop._scope_block``) and the band both already see it. The undeclared lanes are
    the population this bead is about.
    """
    if not paths:
        return (
            "no append-only path declared ([worktree] append_only_paths) - a path every "
            "lane writes is invisible to the grouping until it is listed",
        )
    header = f"append-only: {', '.join(f'`{path}`' for path in paths)}"
    if len(lanes) < 2:
        return (f"{header} - {len(lanes)} lane(s) in this pass, so nothing contends",)
    scopes = merge.declared_scopes(repo_root, lanes)
    lines = [header]
    for path in paths:
        contending = tuple(lane for lane in lanes if path not in scopes.get(lane, ()))
        if len(contending) < 2:
            continue
        lines.append(
            f"  {len(contending)} lane(s) will each append to `{path}` and none declares it: "
            f"{', '.join(contending)}"
        )
        lines.append(
            "    the later ones rebase onto a moved anchor and bounce, so build them in "
            "sequence, or give one lane the entry"
        )
    return tuple(lines)


def generated_report(paths: tuple[str, ...], command: tuple[str, ...]) -> tuple[str, ...]:
    """What this pass will do with a landing conflict on a rebuildable artifact.

    Reported beside :func:`append_only_report` because the two are the same collision
    with opposite remedies, and an operator who reads only the ``contend:`` line would
    conclude a shared artifact must serialise the pass when it need not (basicly-lyro).

    Says so when nothing is declared, for the reason that report does: the undeclared
    state is the one that costs a lane its rework budget, and it is only ever
    discovered at the merge queue, after the money is spent.
    """
    if not paths:
        return (
            "no generated path declared ([worktree] generated_paths) - a landing conflict "
            "on an artifact every lane rebuilds bounces the lane instead of being rebuilt",
        )
    return (
        f"generated: {', '.join(f'`{path}`' for path in paths)} - a landing conflict confined "
        f"to these is rebuilt with `{' '.join(command)}` and continues, spending no rework",
    )


# --- The spend ceiling at pass admission (D3 looking forward, basicly-jr0l.22) ---
#
# ``policy.spend_status`` compares spend *already recorded* against the grant's
# budget, so a pass is admitted whenever the previous ones happened to fit. On the
# basicly-u6jq.1 proof run a 5000000-token ceiling admitted a pass that spent
# 46026602 and halted on the pass *after* the money was gone. The arithmetic was
# right; it was simply retrospective, and with concurrent lanes one pass can spend
# an unbounded multiple of a budget nothing checked it against.
#
# So the pass now sums what it is about to start and refuses when that will not fit
# the remainder. The fix is emphatically not to interrupt a working agent: this runs
# before anything spawns, in-flight lanes still land through the routing layer, and
# a refusal costs no prompt assembly — the same place and the same reasoning as the
# working-set band above and ``runner.run``'s model refusal.
#
# Two rules keep the sum honest, and both matter more than the total being large:
#
# - **A lane the band already refuses is not counted.** It will not dispatch, so it
#   will not spend; counting it would refuse a pass over money nobody was going to
#   spend, and the two gates would compound into a wedge.
# - **A lane with no forecast is named, never guessed at.** Most open beads carry no
#   ``## Scope``, so a missing forecast is the common case, not the anomaly. Its
#   absence is carried in ``unforecast`` and stated in the message, because the
#   honest reading of this gate is "the lanes that could be forecast do not fit",
#   and a total presented as complete when it is partial is the failure mode
#   basicly-jr0l.21 built the seeded/measured labelling to prevent.
#
# The gate admits when it cannot forecast anything at all, on the same reasoning the
# band admits an un-estimatable lane: failing closed on a missing estimate turns a
# spend governor into a ban on hand-filed work.


# The queue question a forecast-refused pass asks, named once for the same reason
# :data:`SIZING_QUESTION` is: :func:`decisions.enqueue` keys items by
# (issue, kind, question), so a second copy of this string would leak a pending item
# that nothing can find (basicly-jr0l.52). The numbers stay in the *detail*, which is
# not part of the id, so a pass that keeps refusing finds the item it already queued.
#
# It was bound twice — here, and again beside its only consumer 220 lines down. The
# second one won at import, so editing this copy, the one a reader finds beside
# `PassSpendAdmission`, changed nothing at all: verbatim the failure jr0l.52 exists to
# prevent, in the constant that carries jr0l.52's own warning (basicly-tcmy.3).
PASS_SPEND_QUESTION = (
    "re-scope this pass or re-grant: its forecast spend exceeds the remaining budget"
)


@dataclass(frozen=True)
class PassSpendAdmission:
    """Whether the lanes a pass is about to start fit the grant's remaining budget."""

    # None only when the pass has no lanes to start at all. Every dispatching lane
    # now contributes either a forecast or a conservative assumed bound.
    forecast_tokens: int | None
    # None when no ceiling applies: no grant, or an L1 grant with no budget.
    remaining_tokens: int | None
    # The lanes whose real forecasts make up part of *forecast_tokens*, and the ones
    # counted at the unsizeable-lane bound instead — kept apart so a message can never
    # present an assumption as a measurement.
    counted: tuple[str, ...]
    unforecast: tuple[str, ...]
    violation: str | None
    # Lanes with no readable scope, counted at `decompose.unsized_lane_tokens` rather
    # than skipped. Skipping them is what left a pass unbounded (basicly-vz78).
    assumed: tuple[str, ...] = ()
    # Whether that bound came from measured lane actuals or from the declared seed.
    assumed_source: str = ""

    @property
    def refused(self) -> bool:
        """True when this pass must dispatch nothing."""
        return self.violation is not None

    @property
    def coverage(self) -> str:
        """How this pass's total was arrived at — reported whether or not it refused.

        Printed on every pass, because the failure mode this closes was *silent*: a
        pass with no forecast at all returned ``violation=None``, which is
        indistinguishable at the surface from a pass that was checked and fitted
        (basicly-vz78). An operator has to be able to see that a number is an
        assumption before it is the only thing standing between them and the bill.
        """
        if self.forecast_tokens is None:
            return "no lanes to start"
        parts = [f"{self.forecast_tokens} tokens forecast"]
        if self.remaining_tokens is not None:
            parts.append(f"{self.remaining_tokens} remaining under the grant")
        else:
            parts.append("no ceiling applies")
        if self.counted:
            parts.append(f"sized: {', '.join(self.counted)}")
        if self.assumed:
            parts.append(
                f"assumed at the unsizeable-lane bound ({self.assumed_source}): "
                f"{', '.join(self.assumed)}"
            )
        if self.unforecast:
            parts.append(f"UNBOUNDED, no figure at all: {', '.join(self.unforecast)}")
        return "; ".join(parts)

    @property
    def detail(self) -> str:
        """The violation with its forecast coverage spelled out, for the queue item."""
        if self.violation is None:
            return ""
        return f"{self.violation} ({self.coverage})"


def admit_pass_spend(
    repo_root: Path,
    working_sets: tuple[WorkingSetAdmission, ...],
    status: policy.SpendStatus,
    sizing: SizingConfig,
) -> PassSpendAdmission:
    """Judge the pass's combined forecast spend against the grant's remainder.

    *working_sets* are the band admissions already computed for this pass, so the
    forecast is built on the estimate that gates each lane rather than on a second
    reading of the same beads.

    A lane whose scope cannot be read is counted at :func:`decompose.unsized_lane_tokens`
    instead of being dropped. Dropping it is what made the gate inert for most of a
    real tracker: with nothing counted the function returned ``violation=None``, which
    ``refused`` reads as "admit", so a pass of unsizeable lanes had no forward bound at
    all (basicly-vz78). An assumed bound can be wrong; no bound cannot be right.

    Never raises. An unreadable history still yields a bound, because the fallback's
    own seed needs no I/O.
    """
    dispatching = tuple(item for item in working_sets if not item.refused)
    if not dispatching:
        return PassSpendAdmission(None, status.remaining_tokens, (), (), None)
    sized = tuple(item for item in dispatching if item.sizing is not None)
    forecasts: tuple[decompose.SpendForecast, ...] = ()
    if sized:
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            forecasts = decompose.dispatch_spend_forecasts(
                repo_root, tuple(item.sizing for item in sized if item.sizing is not None), sizing
            )
    counted: list[str] = []
    total = 0
    if forecasts:
        # strict: `dispatch_spend_forecasts` returns one forecast per sizing, so a
        # length mismatch is a bug, not a lane to skip. The empty tuple a suppressed
        # read leaves behind is handled by not entering the loop at all.
        for item, forecast in zip(sized, forecasts, strict=True):
            if forecast.tokens is None:
                continue
            counted.append(item.issue_id)
            total += forecast.tokens
    # Everything the real forecast could not cover — an absent scope, a None-token
    # forecast, or a suppressed read — is bounded at the same conservative figure
    # rather than waved through.
    assumed = tuple(
        item.issue_id for item in dispatching if item.issue_id not in frozenset(counted)
    )
    assumed_source = ""
    if assumed:
        per_lane, assumed_source = decompose.unsized_lane_tokens(repo_root, sizing)
        total += per_lane * len(assumed)
    return PassSpendAdmission(
        forecast_tokens=total,
        remaining_tokens=status.remaining_tokens,
        counted=tuple(counted),
        # Nothing is left without a figure now; the field stays for a caller that
        # still wants to distinguish "no bound" and for the message to stay honest if
        # a future path reintroduces one.
        unforecast=(),
        violation=policy.check_pass_spend(total, status),
        assumed=assumed,
        assumed_source=assumed_source,
    )


def _report_coverage(
    report: Callable[[str], None] | None,
    working_sets: tuple[WorkingSetAdmission, ...],
    pass_spend: PassSpendAdmission,
) -> None:
    """Emit what each cost gate covered, before the pass dispatches anything.

    Both lines together, because they answer one question in two halves — what the
    band measured, and what the spend total is made of — and an operator who sees only
    one of them is back to reading a partial check as a complete one.
    """
    if report is None:
        return
    report(f"band:     {band_coverage(working_sets)}")
    report(f"spend:    {pass_spend.coverage}")


UNGRANTED_QUESTION = (
    "this session dispatches a metered agent but carries no grant with a token budget: "
    "issue one, or set [runner] default to the manual handoff?"
)


def metered_without_a_budget(repo_root: Path, admission: policy.SpendStatus) -> str | None:
    """The configured runner's name when it meters spend under no budget, else None.

    Hoisted out of :func:`dispatch_lanes` so a caller can ask *before* doing expensive
    setup. Seeding provisions a worktree per lane — a ``uv sync`` and an ``npm install``
    each — and doing five of those to then refuse the dispatch is minutes of work for a
    pass that could never have started (basicly-kkux).
    """
    if admission.grant is not None and admission.grant.token_budget is not None:
        return None
    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    return spec.name if spec.kind == runner.HEADLESS else None


def record_ungranted_refusal(
    repo_root: Path, root_issue: str, runner_name: str, lanes: tuple[AdoptedLane, ...]
) -> str:
    """Refuse a metered pass that no budget covers; the detail reported and queued.

    Both halves of D3's ceiling are keyed on the grant, so with none there is no bound
    at all rather than a loose one — ``spend_status`` reports ``halted=False`` and
    ``check_pass_spend`` admits any forecast against a ``None`` remainder
    (basicly-kkux). The grant is also the authorization: it is the one human confirm
    that permits delegated spend, so dispatching a metered runner without it spends
    money nobody approved.

    Queued as well as reported, like every other spend refusal, so a client that only
    reads the decision queue does not see this as an idle pass.
    """
    detail = (
        f"no grant with a token budget covers {root_issue}, so the {runner_name!r} runner "
        f"has no ceiling to meter {len(lanes)} ready lane(s) against; issue one with "
        f"`basicly policy grant {root_issue} --level L1 --token-budget N` or switch "
        "[runner] default to the manual handoff"
    )
    decisions.enqueue(repo_root, root_issue, "escalation", UNGRANTED_QUESTION, detail)
    return detail


def record_pass_refusal(
    repo_root: Path, root_issue: str, admission: PassSpendAdmission
) -> decisions.DecisionItem:
    """Surface a forecast-refused pass to the human as a queue item on the root.

    The same shape :func:`record_dispatch_halt` gives the retrospective halt, and
    for the same reason: the pass would otherwise just stop dispatching and a client
    would read it as "no ready lanes". Idempotent per (issue, kind, question), so a
    pass that keeps refusing re-enqueues the one item rather than piling up
    notifications — the numbers live in the *detail*, which is not part of the id.
    """
    return decisions.enqueue(
        repo_root,
        root_issue,
        "escalation",
        PASS_SPEND_QUESTION,
        admission.detail,
    )


# --- Concurrent dispatch: fan ready lanes out up to the cap ------------------


@dataclass(frozen=True)
class LaneOutcome:
    """What one lane dispatch produced, for the routing layer (kjc5.7) and the CLI."""

    issue_id: str
    runner_name: str
    # None when the lane could not dispatch (no worktree session record).
    result: runner.RunResult | None
    # The agent's structured "missing fact" signal, consumed from its worktree.
    needs_fact: str | None
    # Final context occupancy in tokens; None when the adapter reports none.
    occupancy: int | None
    overrun: bool
    followup_id: str | None
    detail: str
    # False for a lane carried into this pass with its work already committed:
    # no runner ran, so a null result means "nothing to implement", not
    # "the dispatch failed" (basicly-kjc5.18).
    dispatched: bool = True
    # True when the engine refused to start this lane at all — nothing spawned, and
    # a queue item now holds it. Distinct from a failed run (basicly-jr0l.16): a
    # deterministic refusal cannot be fixed by re-running it, so it must route to
    # the queue rather than burn the bounded dispatch retries.
    refused: bool = False
    # True when a hard-killed dispatch's worktree was committed on its way out
    # (basicly-yvx9). Only ever set beside a timed-out result, and it is what tells
    # the routing there is a diff to judge: a killed run with nothing committed has
    # nothing for the landing to say anything about, and parks on its queue item.
    salvaged: bool = False
    # True when the dispatch died on a *transient* failure of the tracker's storage
    # layer rather than on anything about this lane (basicly-vkh0.10). Nothing
    # spawned and the lane's tree is untouched, so charging it a dispatch rework
    # attempt is charging the lane for the store's contention: on the 2026-08-02
    # five-lane pass that is exactly what parked `basicly-tcmy.11`, which reached the
    # rework cap without an agent ever starting. Distinct from `refused`, which is
    # deterministic and cannot be fixed by re-running, and from a plain failure,
    # which is about the work.
    transient: bool = False
    # Which model actually did the work, and whether that is the one asked for
    # (basicly-e5a6). "via claude" names the *adapter*, which says nothing about the
    # tier — and tier resolution is the whole point of the models map, so a run that
    # silently resolved to a cheaper or dearer model than intended read identically to
    # a correct one. Requested tier and resolved id are knowable before the run;
    # `observed` and `honoured` only after it, so they are separate fields rather than
    # one summary.
    model: str | None = None
    model_tier: str | None = None
    model_source: str | None = None
    observed_models: tuple[str, ...] = ()
    tier_honoured: bool | None = None

    @property
    def model_note(self) -> str:
        """One compact clause naming the model identity, or "" when nothing is known."""
        parts: list[str] = []
        if self.model_tier:
            asked = f"tier {self.model_tier}"
            if self.model_source:
                asked += f" via {self.model_source}"
            parts.append(asked)
        if self.model:
            parts.append(f"model {self.model}")
        observed = tuple(m for m in self.observed_models if m)
        # Reported when it disagrees with the pin, and when nothing was pinned at all —
        # the second case is how a dispatch with no declared tier still says what ran.
        if observed and (self.model is None or set(observed) != {self.model}):
            parts.append(f"observed {', '.join(observed)}")
        if self.tier_honoured is False:
            parts.append("TIER NOT HONOURED")
        return "; ".join(parts)


def ready_lanes(
    repo_root: Path, session: SessionState, *, skip: frozenset[str] = frozenset()
) -> tuple[AdoptedLane, ...]:
    """The session's dispatchable lanes: adopted, live, and unblocked per ``br``.

    Readiness is re-checked at pass time, because a dependency edge added since
    provisioning (e.g. a found-info coupling) must gate the lane *now*. The gate
    is blocked-ness plus an empty decision queue for the lane, not ready-list
    membership: a provisioned lane is claimed (in_progress), and ``br
    scheduler`` recommends only unclaimed work — so the scheduler's rank orders
    the lanes it does know, and the rest follow in adoption order.

    *skip* drops lanes the caller is handling without a runner this pass — the
    ones carried forward to land (basicly-kjc5.18).

    A lane whose status is not dispatchable (``deferred``) is still *adopted* —
    dropping it from :func:`derive_session` would hide its worktree from landing
    and from binding repair — but it takes no runner here (basicly-toj6).
    """
    blocked = set(loop_state.blocked_ids(repo_root))
    ranks = {node.issue_id: node.rank for node in loop_state.ready_ranked(repo_root)}
    live = [
        lane
        for lane in session.adopted
        if lane.live
        and loop_state.is_dispatchable(lane.status)
        and lane.issue_id not in blocked
        and lane.issue_id not in skip
        # A lane waiting on a queued judgment must not burn a dispatch that
        # will only re-block on the same missing answer (basicly-kjc5.7).
        and not decisions.has_pending(repo_root, lane.issue_id)
        # Only build-phase lanes take a runner: a landed lane parked in
        # verify/ship must be advanced (see advance_parked), never handed a
        # fresh implement-and-commit run against an already-merged branch.
        and _phase_of(repo_root, lane.issue_id) == "build"
        # A lane carrying sub-task beads is excluded for the mirror-image reason
        # (basicly-kjc5.9, D7): the loop's lane mini-loop drives it, dispatching
        # one fresh runner per sub-task inside the lane worktree, so dispatching
        # the lane bead itself would re-implement the whole package in one run.
        and not _has_subtasks(repo_root, lane.issue_id)
    ]
    return tuple(
        sorted(live, key=lambda lane: (ranks.get(lane.issue_id, float("inf")), lane.issue_id))
    )


def _phase_of(repo_root: Path, issue_id: str) -> str:
    """The lane's derived loop phase (pure read; br is the state)."""
    return loop_state.read_node_state(repo_root, issue_id).phase


def _has_subtasks(repo_root: Path, issue_id: str) -> bool:
    """True when *issue_id* was split into sub-task beads (a mini-loop lane, D7).

    Status-agnostic on purpose: a lane whose sub-tasks have all closed is waiting
    to integrate, not to be re-implemented, so it must stay out of the top-level
    dispatch set as much as one still working through them.
    """
    record = _show_issue(repo_root, issue_id) or {}
    return any(
        isinstance(dep, dict) and dep.get("dependency_type") == "parent-child"
        for dep in record.get("dependents") or []
    )


def configure_budget(repo_root: Path) -> runner.ProcessBudget:
    """Install the session's global agent-process budget from config (component 8).

    ``[runner] max_agent_processes`` is the ceiling and ``[worktree] concurrency``
    is the lane reservation, so the two knobs a consumer already sets determine
    the whole split — there is nothing extra to configure. Called once per
    supervisor start: D1 makes this process the owner of the machine's
    concurrency, and the budget must not be re-derived while slots are held.
    """
    return runner.configure_process_budget(
        load_runner_config(repo_root).max_agent_processes,
        load_worktree_config(repo_root).concurrency,
    )


def record_dispatch_halt(
    repo_root: Path, root_issue: str, admission: policy.SpendStatus
) -> decisions.DecisionItem:
    """Surface a spend halt to the human as a queue item on the session root.

    D3's halt is silent otherwise: the pass would simply stop dispatching and a
    client would read it as "no ready lanes". The item is idempotent per
    (issue, kind, question), so every subsequent halted pass re-enqueues the
    same one rather than piling up notifications.

    The two halts ask different questions, so they are different queue items
    (basicly-jr0l.35): a spent budget is answered by deciding whether the work is
    worth more money, an unmeterable one by fixing what the harness can see. Putting
    both behind "the budget is spent" would send the operator to re-grant a budget
    that was never the problem.
    """
    question = (
        UNMETERED_QUESTION
        if admission.unmetered_dispatches
        else "re-grant autonomy or continue by hand: the session's token budget is spent"
    )
    return decisions.enqueue(repo_root, root_issue, "escalation", question, admission.detail)


UNMETERED_QUESTION = (
    "the runner reported no measurable usage for a dispatch under this grant, so the spend "
    "ceiling cannot bind: configure a runner whose usage basicly can read, or re-grant to "
    "accept the unmetered spend"
)


# --- Autonomous delegation: the decider in the pass (D3 L2+, design 7.1) -----


# Decision kinds the decider may take at L2+ (design 7.1: "approve delegable
# checkpoints, triage escalations, and answer needs-input questions"). The three
# excluded kinds are excluded on purpose:
#
# - ``checkpoint`` — the delegable-checkpoint path *is*
#   ``policy._grant_approval``, and it already ran and refused before this item
#   was enqueued. Answering the item would clear the lane's hold without the
#   checkpoint ever being approved, routing around the ladder and the L3
#   preconditions D3 makes ship conditional on.
# - ``validate`` — a judged NO re-judged by another agent is the consensus-voting
#   shape D9 rejects by name; R4 wants a human on an unmet acceptance criterion.
# - ``stall`` — a hard-killed runner is an operational fact about a process, not a
#   question the intake corpus can answer.
DELEGABLE_KINDS = ("escalation", "needs-input")

# L2 is where delegation begins (D3): L0 is task-by-task and L1 only pre-approves
# the decompose checkpoint at intake.
_MIN_DELEGATION_LEVEL = "L2"


@dataclass(frozen=True)
class DelegatedDecision:
    """One decider invocation's outcome, for the pass report."""

    decision_id: str
    issue_id: str
    kind: str
    # True when the decider decided and the answer is recorded; False when the
    # item stayed with the human (abstained, unparseable, capped, unconfinable).
    answered: bool
    detail: str


def _delegation_allowed(grant: policy.Grant | None) -> bool:
    """True when the grant's level reaches L2, where D3 starts delegating."""
    if grant is None:
        return False
    levels = AUTONOMY_LEVELS
    if grant.level not in levels:
        return False
    return levels.index(grant.level) >= levels.index(_MIN_DELEGATION_LEVEL)


def delegate_decisions(
    repo_root: Path,
    session: SessionState,
    *,
    beat: Callable[[], None] | None = None,
    admission: policy.SpendStatus | None = None,
) -> tuple[DelegatedDecision, ...]:
    """Ask the decider to resolve the session's delegable pending items (D3 L2+).

    This is the autonomous path: without it an L2 grant delegates nothing,
    because a pending item only ever *holds* its lane
    (:func:`ready_lanes`). Run before dispatch in a pass, so an item the decider
    answers releases its lane in that same pass.

    Serial by design — the decider is one reserved process, not a fan-out — and
    *beat* is invoked between invocations so a slow decider never lets the
    singleton lock go stale. Every drop-to-human cause (abstention, unparseable
    verdict, ``decider_max_decisions``, an unconfinable runner family, the spend
    halt) is decided inside :func:`decisions.invoke_decider`; this layer only
    chooses *which* items to offer it and reports what came back.
    """
    if admission is None:
        admission = policy.spend_status(repo_root, session.root_issue)
    # No delegation without a covering grant, and none past D3's spend ceiling —
    # invoke_decider re-checks the ceiling, but stopping here skips a tracker walk
    # per item on a session that can no longer delegate anything.
    if admission.halted or not _delegation_allowed(admission.grant):
        return ()
    delegated: list[DelegatedDecision] = []
    for item in decisions.pending(repo_root, session.root_issue):
        if item.kind not in DELEGABLE_KINDS:
            continue
        if beat is not None:
            beat()
        delegated.append(_delegate_one(repo_root, item, session.root_issue))
    return tuple(delegated)


def _delegate_one(
    repo_root: Path, item: decisions.DecisionItem, root_issue: str
) -> DelegatedDecision:
    """Offer one item to the decider; a failed invocation leaves it with the human."""
    try:
        outcome = decisions.invoke_decider(repo_root, item.decision_id, root_issue)
    except (RuntimeError, OSError, ValueError) as exc:
        # Per-item containment, matching the lane dispatch stance: one broken
        # delegation must not abort the pass and strand every other decision.
        return DelegatedDecision(
            decision_id=item.decision_id,
            issue_id=item.issue_id,
            kind=item.kind,
            answered=False,
            detail=f"decider invocation failed: {exc}",
        )
    if isinstance(outcome, decisions.DecisionItem):
        return DelegatedDecision(
            decision_id=outcome.decision_id,
            issue_id=outcome.issue_id,
            kind=outcome.kind,
            answered=not outcome.pending,
            detail=f"{outcome.answered_by}: {outcome.answer}",
        )
    return DelegatedDecision(
        decision_id=item.decision_id,
        issue_id=item.issue_id,
        kind=item.kind,
        answered=False,
        detail=outcome.rationale or "not derivable from the corpus",
    )


def dispatch_lanes(  # noqa: PLR0913 — each arg is one independent pass-scoped input
    repo_root: Path,
    session: SessionState,
    *,
    beat: Callable[[], None] | None = None,
    cap: int | None = None,
    skip: frozenset[str] = frozenset(),
    admission: policy.SpendStatus | None = None,
    report: Callable[[str], None] | None = None,
) -> tuple[LaneOutcome, ...]:
    """Dispatch the session's ready lanes concurrently, honoring the cap.

    The cap defaults to ``[worktree] concurrency`` — one runner per provisioned
    lane, matching the fan-out that created the worktrees. While dispatches run,
    *beat* is invoked every :data:`HEARTBEAT_INTERVAL_S` so the singleton lock
    never goes stale mid-pass; a :class:`LockLostError` from it cancels every
    lane not yet started and propagates immediately. Runners already executing
    are not killed — their commits and run-records complete on their branches,
    and the successor supervisor re-adopts the lanes from ``br`` (recovery is
    derivation). Outcomes return in dispatch (scheduler-rank) order.

    Dispatch is *admitted* only while the session is inside D3's spend ceiling
    (basicly-kjc5.23), measured two ways. Backward: a session whose recorded spend
    has reached the budget starts nothing new. Forward: a pass whose lanes are
    together forecast to overrun what is left starts nothing new either
    (basicly-jr0l.22) — the retrospective half alone admitted a pass that spent 9x
    its ceiling and only noticed afterwards. Either way in-flight lanes still land
    through the routing layer, no running agent is ever interrupted, and the refusal
    is enqueued on the root so the human learns what is required. *admission* lets a
    caller that already read the status pass it in; omitting it re-reads here, so no
    dispatch path can bypass the ceiling by forgetting to check.

    *skip* excludes lanes the caller lands without a runner (basicly-kjc5.18).

    *report* receives the pass's band and spend coverage before anything is dispatched
    — which lanes the band measured and which it could not (basicly-jr0l.60), then how
    the spend total was reached and which lanes are counted at an assumed bound rather
    than a real forecast. Emitted on the admitted path too, deliberately: the defect
    both close was that an unbounded pass looked exactly like a checked one
    (basicly-vz78).
    """
    lanes = ready_lanes(repo_root, session, skip=skip)
    if not lanes:
        return ()
    # Readiness first, then admission: the queue item records a *refusal*, so a
    # halted session with nothing ready has nothing to escalate yet. It escalates
    # on the first pass where the ceiling actually stops ready work.
    if admission is None:
        admission = policy.spend_status(repo_root, session.root_issue)
    if admission.halted:
        record_dispatch_halt(repo_root, session.root_issue, admission)
        return ()
    if cap is None:
        cap = load_worktree_config(repo_root).concurrency
    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    sizing = load_sizing_config(repo_root)

    # A metered runner needs a budget to be metered against. Both halves of D3's
    # ceiling key on the grant — `spend_status` reports `halted=False` and
    # `remaining_tokens=None` when there is none, and `check_pass_spend` admits
    # anything against a None remainder — so an ungranted session had no bound at all
    # (basicly-kkux). Latent while the supervisor could not seed its own lanes, and one
    # command deep once basicly-t73d let it. A handoff spends nothing, so it proceeds.
    ungranted = admission.grant is None or admission.grant.token_budget is None
    if spec.kind == runner.HEADLESS and ungranted:
        detail = record_ungranted_refusal(repo_root, session.root_issue, spec.name, lanes)
        if report is not None:
            report(f"refused:  {detail}")
        return ()

    # Size every lane before any of them starts. The band needs this per lane and
    # the pass-spend gate needs all of them summed, so it is read once here and
    # handed down — the same hoist basicly-jr0l.16 made of the estimate the dispatch
    # record already carried, extended by one level because the question is now
    # about the pass rather than the lane.
    working_sets = tuple(admit_working_set(repo_root, lane.issue_id, sizing) for lane in lanes)
    pass_spend = admit_pass_spend(repo_root, working_sets, admission, sizing)
    _report_coverage(report, working_sets, pass_spend)
    if pass_spend.refused:
        record_pass_refusal(repo_root, session.root_issue, pass_spend)
        return ()
    banded = {item.issue_id: item for item in working_sets}

    # Read once for the whole pass, not per lane: every lane must be recorded
    # against the *same* ranking, or the pass ordering it explains is a blend of
    # several answers and reconstructs to nothing (D9, basicly-vkh0.3). `lanes` is
    # already in dispatch order, so a lane's position in it is the ordering key
    # actually used — including for the lanes br never ranked, which is most of
    # them once they are claimed.
    ranking = loop_state.ready_ranking(repo_root)
    ranked = ranking.by_issue()
    dispatch_ranks = {lane.issue_id: position for position, lane in enumerate(lanes, start=1)}

    # Stamped inside the worker rather than at submit time: with more ready lanes than
    # the cap, the extras sit in the pool queue, and counting that wait as run time
    # would report an elapsed figure for a lane that has not started (basicly-vu6u).
    started: dict[str, float] = {}

    def guarded(lane: AdoptedLane) -> LaneOutcome:
        # Re-read the ceiling at the moment this lane actually starts, not when the
        # pass was admitted. Lanes past the concurrency cap wait in the pool queue,
        # and the spend that exhausted the grant can accrue while they wait — the one
        # pass-entry verdict could not see it (basicly-jr0l.59). A lane already running
        # is never interrupted; this only declines to *start* one.
        live = policy.spend_status(repo_root, session.root_issue)
        if live.halted:
            return LaneOutcome(
                issue_id=lane.issue_id,
                runner_name=spec.name,
                result=None,
                needs_fact=None,
                occupancy=None,
                overrun=False,
                followup_id=None,
                detail="not started: the grant was exhausted while this lane waited for a slot",
            )
        started[lane.issue_id] = time.monotonic()
        # Per-lane containment: a transient br failure (e.g. a locked tracker
        # DB under this very concurrency) or an OS hiccup in one lane must not
        # discard every other lane's outcome at collection time.
        try:
            return _dispatch_lane(
                repo_root,
                session,
                lane,
                spec,
                sizing,
                ordering=DispatchOrdering(
                    dispatch_rank=dispatch_ranks.get(lane.issue_id),
                    node=ranked.get(lane.issue_id),
                    policy=ranking.schema,
                ),
                working_set=banded.get(lane.issue_id),
            )
        except (RuntimeError, OSError, ValueError) as exc:
            return LaneOutcome(
                issue_id=lane.issue_id,
                runner_name=spec.name,
                result=None,
                needs_fact=None,
                occupancy=None,
                overrun=False,
                followup_id=None,
                detail=f"lane dispatch failed: {exc}",
                # Classified here rather than at the routing layer because this is
                # the only frame that knows nothing ran: a nonzero *runner* exit
                # quoting the same text is about the agent's work, not the store.
                transient=br.is_transient_storage_error(str(exc)),
            )

    pool = ThreadPoolExecutor(max_workers=max(1, cap))
    try:
        futures = [pool.submit(guarded, lane) for lane in lanes]
        by_future = dict(zip(futures, lanes, strict=True))
        pending = set(futures)
        while pending:
            timeout = HEARTBEAT_INTERVAL_S if (beat or report) else None
            _done, pending = wait(pending, timeout=timeout)
            if pending and beat is not None:
                beat()
            if pending and report is not None:
                # A lane emits nothing between adoption and completion, so a whole
                # multi-minute run looked identical to a wedge (basicly-vu6u). Measured:
                # the log stood still for 519.6s on a healthy lane, and `pgrep` was the
                # only way to tell. Reported from the heartbeat that already runs here.
                report(f"running:  {_inflight_note(started, by_future, pending)}")
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return tuple(future.result() for future in futures)


def _inflight_note(
    started: dict[str, float],
    by_future: dict[Future[LaneOutcome], AdoptedLane],
    pending: set[Future[LaneOutcome]],
) -> str:
    """One line naming each still-running lane and how long it has been running.

    Elapsed only, deliberately. Tokens-so-far would be the other half an operator wants,
    but the runner drains its pipes only after the process is down (basicly-kjc5.15, and
    see :func:`lane_activity`), so there is nothing incremental to read without
    restructuring the dispatch — tracked separately rather than half-done here.

    A monotonic clock, because this is a duration; a wall clock can step backwards and
    report a lane as having run for a negative time.
    """
    now = time.monotonic()
    running = sorted(
        (lane.issue_id, now - started[lane.issue_id])
        for future, lane in by_future.items()
        if future in pending and lane.issue_id in started
    )
    if not running:
        # Every pending lane is still queued behind the cap, which is itself worth
        # saying: the operator would otherwise read the silence as a stall.
        queued = len(pending)
        return f"{queued} lane(s) queued behind the concurrency cap, none started yet"
    return ", ".join(f"{issue_id} {elapsed:.0f}s" for issue_id, elapsed in running)


def lane_activity(cwd: Path) -> str:
    """A fingerprint of a lane's visible progress: its commits plus its dirty tree.

    The two things a working lane changes. Reading the agent's stdout would be the
    other signal, but the runner drains its pipes only after the process is down
    (basicly-kjc5.15), so there is nothing incremental to sample; commits and file
    writes are both cheaper and closer to what "progress" means for a lane.
    """
    head = subprocess.run(
        ["git", "-C", str(cwd), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        check=False,
    )
    dirty = subprocess.run(
        ["git", "-C", str(cwd), "status", "--porcelain"],
        capture_output=True,
        text=True,
        check=False,
    )
    return hashlib.sha256(f"{head.stdout}\n{dirty.stdout}".encode()).hexdigest()


# The mid-run stall flag's question, named once so :func:`resolve_stall_flag` can
# find the item :func:`flag_stalled_lane` queued. The two must agree exactly — the
# queue keys items by (issue, kind, question) — and a copy of this string in two
# places is a silent leak of pending items (basicly-jr0l.52).
STALL_FLAG_QUESTION = "lane may be stuck: intervene now or let the hard kill arrive?"


def flag_stalled_lane(
    repo_root: Path, issue_id: str, stall_after: float, runner_timeout: float
) -> decisions.DecisionItem:
    """Queue a lane as possibly-stuck, leaving the run to continue (design section 6).

    Idempotent per (issue, kind, question), so a lane is flagged once however many
    times it is sampled. The item names the hard kill deliberately: the human's
    real choice is whether to intervene now or let the timeout arrive.

    Because the question is only meaningful *while* the run is in flight,
    :func:`resolve_stall_flag` disposes of it as soon as the dispatch ends.
    """
    return decisions.enqueue(
        repo_root,
        issue_id,
        "stall",
        STALL_FLAG_QUESTION,
        # :g rather than :.0f — a sub-second stall_after (tests, tight configs)
        # otherwise reads as "0s", which says the opposite of what happened.
        f"no commits and no file changes for {stall_after:g}s; the run continues "
        f"until runner_timeout ({runner_timeout:g}s), still holding a lane slot",
    )


def resolve_stall_flag(repo_root: Path, issue_id: str) -> tuple[str, ...]:
    """Auto-answer *issue_id*'s mid-run stall flags; the ids disposed of.

    The flag asks whether to intervene *before* the hard kill arrives, so the moment
    the dispatch ends the question can no longer be acted on — either the kill
    arrived (and enqueued its own, answerable item) or the run finished and there is
    nothing to intervene in. Left pending it is worse than useless: ``has_pending``
    drops the lane from ``ready_lanes`` and from the carry, so a lane that was merely
    slow parks until a human clears a question with no live subject
    (basicly-jr0l.52).

    Answered rather than deleted, so the audit trail still shows the lane was flagged
    and why the flag stopped mattering. Scans by question instead of recomputing the
    content-derived id, because a re-opened item carries a generation suffix.

    The engine disposing of its own moot question is not a human decision, so it is
    recorded as delegated and never lands in the human-wait column (D11).
    """
    disposed: list[str] = []
    for item in decisions.items_on(repo_root, issue_id):
        if item.kind == "stall" and item.question == STALL_FLAG_QUESTION and item.pending:
            decisions.answer(
                repo_root,
                item.decision_id,
                "dispatch ended; nothing left to intervene in before a hard kill",
                by=decisions.ENGINE_BY,
            )
            disposed.append(item.decision_id)
    return tuple(disposed)


@dataclass(frozen=True)
class DispatchOrdering:
    """Why one lane went when it did, recorded on its run marker (basicly-vkh0.3).

    *node* is br's scheduler evidence and is None whenever br did not rank the
    lane — the ordinary case, since a provisioned lane is claimed and ``br
    scheduler`` recommends only unclaimed work. *dispatch_rank* is always known,
    so the pass ordering stays reconstructible either way.
    """

    dispatch_rank: int | None
    node: loop_state.RankedNode | None
    policy: str

    def as_inputs(self) -> dict[str, object]:
        """The recorded-dispatch keywords this ordering contributes."""
        return {
            "dispatch_rank": self.dispatch_rank,
            "scheduler_rank": self.node.rank if self.node else None,
            "scheduler_fallback_rank": self.node.fallback_rank if self.node else None,
            "scheduler_score": self.node.score if self.node else None,
            # Recorded even when br did not rank this lane: the policy is a
            # property of the pass, and it says which version produced the
            # ordering the other lanes were sorted by.
            "scheduler_policy": self.policy or None,
        }


def record_unstarted_dispatch(
    repo_root: Path, issue_id: str, spec: runner.RunnerSpec, error: BaseException
) -> None:
    """Record a dispatch that died before its agent process started (basicly-jr0l.64).

    The engine's own captured error is the entire transcript of such a dispatch, so
    it goes on the record as the run's output: the chars/4 floor over it is a real
    bound on the cost rather than the structural under-count the same floor is for
    an agent run, and ``run_record.UNSTARTED`` is what says so. That label is the
    whole point — without it ``policy.session_spend`` reads the floor as an
    unmeterable *run* and halts the grant over a dispatch that spawned nothing.

    Telemetry, so it never raises: ``runner.record_dispatch`` already suppresses its
    own write errors, and the caller is on its way to re-raising the real failure.
    """
    runner.record_dispatch(
        repo_root,
        issue_id,
        spec,
        runner.RunResult(spec.name, (), executed=False, stderr=redact_secrets(str(error))),
        phase=run_record.LANE_PHASE,
    )


def _dispatch_lane(  # noqa: PLR0913 — one parameter per independent lane input
    repo_root: Path,
    session: SessionState,
    lane: AdoptedLane,
    spec: runner.RunnerSpec,
    sizing: SizingConfig,
    ordering: DispatchOrdering | None = None,
    working_set: WorkingSetAdmission | None = None,
) -> LaneOutcome:
    """Run one lane: assemble its bundle now, dispatch, record, and meter.

    *working_set* lets the pass hand down the band admission it already computed to
    sum the pass forecast (basicly-jr0l.22); omitting it re-estimates here, so a
    caller that forgets cannot dispatch an unsized lane past the band.
    """
    record = worktree.load_session(lane.binding.name, repo_root)
    if record is None:
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=None,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            followup_id=None,
            detail=f"worktree {lane.binding.name!r} has no session record; re-provision the lane",
        )
    # Sized before the dispatch, not after: the estimate has to describe the tree the
    # agent was handed, not the one it left behind (basicly-kjc5.30). And before the
    # bundle rather than beside the run, because the band now *gates* the dispatch
    # (basicly-jr0l.16) — a refusal should cost no prompt assembly, for the same
    # reason ``runner.run`` resolves its model before it spawns anything.
    admission = working_set
    if admission is None:
        admission = admit_working_set(repo_root, lane.issue_id, sizing)
    queued = escalate_working_set(repo_root, admission)
    if admission.refused:
        held = f"; held by {queued.decision_id}" if queued is not None else ""
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=None,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            followup_id=None,
            detail=f"dispatch refused before it started: {admission.violation}{held}",
            refused=True,
        )
    lane_sizing = admission.record_inputs(repo_root)
    if not lane_sizing:
        # A lane with no readable scope is still dispatched, bounded at the assumed
        # figure — so record that figure as its forecast. Without it the pass is gated
        # on a number the record never carries, and the lane lands as one more actual
        # with no forecast half: after a completed four-lane run `usage forecast` still
        # reported "no dispatch carries both", 17 actual with no forecast. The
        # telemetry that would calibrate the bound was the one thing the bound's own
        # dispatches never produced (basicly-jr0l.58).
        #
        # It lands on the **spend** field, because that is the quantity it is
        # denominated in: `unsized_lane_tokens` is a quantile of measured lane actuals,
        # so writing it to `forecast_tokens` put a whole-lane cost in the working-set
        # slot and paired it against a whole-lane actual at a ratio of ~1x — a forecast
        # that looks perfect while predicting the wrong quantity (basicly-tcmy.34).
        assumed_tokens, assumed_source = decompose.unsized_lane_tokens(repo_root, sizing)
        lane_sizing = {
            "forecast_spend_tokens": assumed_tokens,
            # Namespaced, so a reader can never mistake an assumed bound for an
            # estimate derived from this lane's own declared scope.
            "forecast_source": f"assumed:{assumed_source}",
        }
    known = frozenset({session.root_issue, *(cid for cid, _ in session.children)})
    # Everything from here to the dispatch itself is pre-flight — a tracker read, a
    # config read, a prompt assembly, a spawn — so a failure in it means no agent
    # process ever existed. Recorded as such rather than left as a silent hole in the
    # telemetry: the pass otherwise keeps no evidence that the lane was attempted at
    # all, and the meter has nothing to tell this apart from an unmeterable agent run
    # (basicly-jr0l.64). The dispatch itself is inside the guard because its own
    # pre-spawn refusals — an unresolvable model tier, a missing CLI — are the same
    # fact; a failure after the process is up would be mislabelled, which is why the
    # region stops at `runner.run` and the recorded run below sits outside it.
    try:
        bundle = build_bundle(repo_root, lane.issue_id, known_ids=known)
        cwd = Path(record.worktree_path)
        runner_config = load_runner_config(repo_root)
        # A lane draws on the reserved lane slots, so it never waits behind a helper
        # (component 8, basicly-kjc5.11). The watchdog only *flags* a wedge
        # (basicly-kjc5.25) — the timeout below is still the sole terminal action.
        watchdog = runner.StallWatchdog(
            runner_config.stall_after,
            probe=lambda: lane_activity(cwd),
            on_stall=lambda: flag_stalled_lane(
                repo_root, lane.issue_id, runner_config.stall_after, runner_config.runner_timeout
            ),
        )
        with watchdog, runner.process_budget().slot(runner.LANE):
            result = runner.run(
                spec, bundle.prompt, cwd, capture_usage=True, timeout=runner_config.runner_timeout
            )
    except (RuntimeError, OSError, ValueError) as exc:
        record_unstarted_dispatch(repo_root, lane.issue_id, spec, exc)
        raise
    loop.record_run(
        repo_root,
        lane.issue_id,
        spec,
        result,
        prompt=bundle.prompt,
        phase=run_record.LANE_PHASE,
        folded_info=tuple(_folded_ref(info) for info in bundle.folded),
        # The lane dispatch is where the measured 160-420x forecast misses were
        # spent, so it is the dispatch that most needs its forecast recorded beside
        # its actual (basicly-jr0l.34).
        **lane_sizing,
        **(ordering.as_inputs() if ordering else {}),
    )
    # The dispatch has ended, so any mid-run stall flag is moot — retire it here,
    # before the timeout branch below queues the answerable version. Unconditional on
    # purpose: if the kill did arrive, the flag's "intervene before the hard kill?"
    # is superseded by that item, and leaving both pending means answering one does
    # not release the lane (basicly-jr0l.52).
    resolve_stall_flag(repo_root, lane.issue_id)
    if result.timed_out:
        # Consume any sentinel the killed run managed to write — leaving it
        # would mis-attribute the fact to the *next* dispatch after triage.
        stale_needs = needs_input.take(cwd)
        # The tree is where the run's whole value sits, and the kill took the agent
        # out before the commit that is its last step — so the harness commits it
        # (basicly-yvx9). Judged, never trusted: the routing below sends a salvaged
        # lane to the landing, where a red gate reworks it with real findings.
        salvaged = commit.salvage(
            cwd,
            lane.issue_id,
            reason=f"runner_timeout after {runner_config.runner_timeout:.0f}s",
        )
        # Hard-kill stall (design section 6): queue it whatever the salvage found.
        # A timeout is a thing a human should see, and rescuing the diff must not
        # turn one into a silent success — the item is what keeps the kill on the
        # record even when the work goes on to land.
        stall = decisions.enqueue(
            repo_root,
            lane.issue_id,
            "stall",
            f"runner {spec.name} hit runner_timeout "
            f"({runner_config.runner_timeout:.0f}s): retry, re-dispatch, or park?",
            "; ".join(
                part
                for part in (
                    salvaged.detail,
                    stale_needs.fact if stale_needs is not None else "",
                )
                if part
            ),
        )
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=result,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            followup_id=None,
            salvaged=salvaged.committed,
            detail=f"timed out after {runner_config.runner_timeout:.0f}s; "
            f"{salvaged.detail}; stall queued as {stall.decision_id}",
        )
    if result.handoff:
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=result,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            followup_id=None,
            detail="handoff runner: work left to the driving agent",
        )
    needs = needs_input.take(cwd)
    if needs is not None:
        # Durable trace (basicly-kjc5.3): the L3 lights-out precondition counts
        # these markers after the sentinel file is consumed (D3).
        policy.record_needs_input(repo_root, lane.issue_id, needs.fact)
        # And one decision-queue item (basicly-kjc5.4) for `loop answer`.
        decisions.enqueue(repo_root, lane.issue_id, "needs-input", needs.fact, needs.detail)
    # A lane that failed or stopped on a missing fact lands nothing and gets
    # re-dispatched by the routing layer, so it is not a coherent partial landing.
    verdict = meter_context_ceiling(
        repo_root,
        session.root_issue,
        lane.issue_id,
        spec,
        result,
        sizing,
        landed=result.returncode == 0 and needs is None,
    )
    occupancy, overrun, followup_id = verdict.occupancy, verdict.overrun, verdict.followup_id
    if result.returncode != 0:
        detail = f"runner exited {result.returncode}"
    elif needs is not None:
        detail = f"needs input: {needs.detail or needs.fact}"
    elif overrun:
        detail = f"finished but crossed the context ceiling; remainder in {followup_id}"
    else:
        detail = "finished; ready to land"
    # Read off the result rather than re-resolved, for the same reason the run record
    # does it that way (basicly-kjc5.59): a second read of the map could answer
    # differently from the dispatch that actually happened.
    resolution = result.model_resolution
    return LaneOutcome(
        issue_id=lane.issue_id,
        runner_name=spec.name,
        result=result,
        needs_fact=needs.fact if needs is not None else None,
        occupancy=occupancy,
        overrun=overrun,
        followup_id=followup_id,
        detail=detail,
        model=resolution.model if resolution is not None else spec.model,
        model_tier=resolution.tier if resolution is not None else None,
        model_source=resolution.source if resolution is not None else None,
        observed_models=runner.observed_models(spec, result),
        tier_honoured=resolution.honoured if resolution is not None else None,
    )


# --- Outcome routing: green lands, everything else queues (basicly-kjc5.7) ---


# Rework gate name for failed dispatches: bounded like merge/verify rework, so
# a crash-looping runner escalates to the queue instead of retrying forever.
DISPATCH_GATE = "dispatch"
# A separate counter for the dispatches lost to the *store* rather than to the work
# (basicly-vkh0.10). It has to be separate, not merely smaller: a lane that spends its
# dispatch budget on tracker contention arrives at the escalation with nothing to
# triage, which is how a five-lane pass parked a lane that had never run an agent. The
# cap still exists, so termination is unchanged — a store that stays broken escalates
# to a human on its own counter instead of silently retrying forever.
TRACKER_GATE = "tracker-storage"


# Routes that keep the standing loop iterating even without a landing: the
# lane will be re-tried and its termination is bounded elsewhere (the dispatch
# and verify rework caps both escalate into the decision queue, which then
# holds the lane via has_pending). "lane-step" is a mini-loop lane that closed a
# sub-task this pass — bounded by max_subtasks_per_lane plus those same caps;
# "bounced" is a collided lane whose agent re-applies its intent next pass,
# bounded by the same merge rework cap; "re-dispatch" is a lane whose merge a
# landing this pass broke, cancelled before it collided and bounded by the
# dispatch cap. "lane-blocked" is deliberately absent, because such a lane waits
# on an agent or a human exactly like a handoff.
RETRIABLE_ROUTES = (
    "retry",
    "rework",
    "held",
    "lane-step",
    "bounced",
    "re-dispatch",
    # A cleared stale binding changes what the *next* derivation sees, so the pass
    # that cleared it has genuinely unblocked work even though nothing landed
    # (basicly-1koh). Termination is not at risk: the binding is gone, so the same
    # lane cannot be repaired twice.
    "repaired",
    # Newly provisioned lanes exist but are not dispatched until the next derivation
    # reads them (basicly-t73d). Bounded by the worktree cap and by `seed_lanes`
    # returning `seed-blocked` — which is *not* retriable — the moment a root stops
    # producing lanes, so a root that cannot seed ends the session instead of looping.
    "seeded",
)


@dataclass(frozen=True)
class RoutedOutcome:
    """Where one lane's outcome went after collection (design component 5)."""

    issue_id: str
    # "shipped" | "merged" | "retry" | "rework" | "held" | "decision"
    # | "handoff" | "lane-step" | "lane-blocked" | "bounced" | "re-dispatch"
    # | "repaired" | "error"
    route: str
    detail: str

    @property
    def progressed(self) -> bool:
        """True when the session moved (a landing or a ship happened)."""
        return self.route in ("merged", "shipped")


def should_continue(routed: tuple[RoutedOutcome, ...]) -> bool:
    """True when the standing loop has another useful iteration to run.

    Progress (a landing or ship) obviously continues; so does any retriable
    route — its termination is guaranteed by the rework caps escalating into
    the decision queue, which then holds the lane. Everything else means the
    session waits on a human.
    """
    return any(r.progressed or r.route in RETRIABLE_ROUTES for r in routed)


def carried_forward(routed: tuple[RoutedOutcome, ...]) -> frozenset[str]:
    """The lanes whose landing this pass deferred, for the next pass to land first.

    Only the ``held`` route carries: that lane is green and committed, and the
    pass simply ran out of a landable base after an earlier failure. Every other
    route either progressed or means the lane's own work needs changing (rework,
    bounced, retry), which is exactly when a fresh dispatch *is* the right move —
    so the carry lapses and the lane re-enters dispatch normally.

    The carry is an in-process optimization, not state: a supervisor that
    restarts mid-session simply re-dispatches the lane, which is the behavior
    before this existed. Correctness still derives from ``br`` alone.
    """
    return frozenset(r.issue_id for r in routed if r.route == "held")


def _carried_outcome(issue_id: str) -> LaneOutcome:
    """The landing-only outcome for a lane whose work is already committed.

    A lane held by an earlier failed landing (see :func:`carried_forward`) has
    already finished its run and committed on its branch, so the next pass owes
    it a *landing*, not a fresh implement-and-commit dispatch — that would spend
    a full run re-doing work already on the branch (basicly-kjc5.18).
    """
    return LaneOutcome(
        issue_id=issue_id,
        runner_name="(none)",
        result=None,
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="work already committed on the branch; landing without a fresh dispatch",
        dispatched=False,
    )


def route_outcomes(
    repo_root: Path,
    session: SessionState,
    outcomes: tuple[LaneOutcome, ...],
    *,
    beat: Callable[[], None] | None = None,
    carried: Iterable[str] = (),
) -> tuple[RoutedOutcome, ...]:
    """Collect dispatch outcomes: land green lanes as they can, bounce collisions (D5).

    Green lanes go through the single-track engine — ``loop.advance`` is the only
    landing path, so each landing is serial and re-verifying — and they are
    landed in the **dependency order** :func:`merge.landing_order` computes from
    ``br``, not merely in the scheduler rank the outcomes arrive in
    (basicly-kjc5.20). The queue's consume-as-ready stance (kjc5.10) holds here
    too:

    - A **scope collision** bounces back to the owning lane and the pass keeps
      going, and the remaining green lanes still land. A lane's wrong scope
      declaration is its own problem, not a reason to stall everyone. The missed
      coupling — and the brief naming the conflicting paths and both sides, which
      is what gives the lane's next dispatch something to do
      (:func:`_record_bounce_briefs`) — is recorded once the pass is over
      (:func:`_attribute_pass_couplings`) rather than at the bounce, so neither
      can depend on which lanes had landed by then (D9, basicly-kjc5.32). A
      landing that failed *identically* to the lane's previous one escalates
      there and then, refunded rather than charged
      (:func:`_escalate_repeat_bounce`).
    - A lane a *landing this pass* already **broke** — its branch no longer
      merges cleanly, and that landing's paths are why — is cancelled before its
      own landing is attempted and re-dispatched with the collision recorded for
      its next prompt (D6, :func:`_preempt_lane`); that likewise does not hold
      the pass.
    - Any **other** blocked landing (a red gate, an uncommitted worktree) still
      holds the later green lanes (``held``) — that is a signal about the base,
      and they re-land next iteration on top of whatever fix lands first.

    *carried* names the lanes the previous pass held: their work is committed
    already, so they are landed here **without** a dispatch having run for them
    this pass (basicly-kjc5.18), ahead of freshly dispatched lanes at equal
    dependency rank because their work is the older of the two.

    Blocked shapes route to the decision queue: a needs-input fact and a timeout
    stall were queued at dispatch, a failed run retries under the bounded rework
    cap and escalates at it, and a landed lane whose ship checkpoint no grant
    covers queues a checkpoint request for the human. A hard-killed lane whose
    worktree was salvaged is the one shape that does both — the stall item holds
    the *timeout* for a human while the *diff* goes to the landing to be judged
    (basicly-yvx9).

    *beat* fires between outcomes; per-outcome failures are contained to that
    lane's route so one br hiccup cannot discard the rest of the pass. Outcomes
    are returned in the order they were processed (landing order), not in the
    order they came in.
    """
    routed: list[RoutedOutcome] = []
    landing_blocked = False
    # (bead, paths its landing added to the base) per landing this pass — the
    # evidence D6's pre-empt reads to name the landing that broke a pending merge
    # (merge.missed_couplings, via _invalidated_by).
    landed: list[tuple[str, tuple[str, ...]]] = []
    # (bead, conflicting paths) per collision, attributed after the pass rather
    # than here: see _attribute_pass_couplings (D9, basicly-kjc5.32).
    collisions: list[tuple[str, tuple[str, ...]]] = []
    pass_outcomes = _carried_outcomes(repo_root, session, carried, outcomes) + outcomes
    for outcome in _landing_order(repo_root, pass_outcomes):
        if beat is not None:
            beat()
        # A salvaged timeout is not green — the run was killed — but it does try to
        # land, so it needs the same pre-landing head sha and the same "stop landing
        # after a failure this pass" treatment as a green lane (basicly-yvx9).
        lands = _is_green(outcome) or outcome.salvaged
        if landing_blocked and lands:
            routed.append(
                RoutedOutcome(
                    outcome.issue_id,
                    "held",
                    "landing paused after an earlier failure this pass",
                )
            )
            continue
        before = merge.head_sha(repo_root) if lands else ""
        try:
            one = _route_one(repo_root, session, outcome, landed, collisions)
        except (RuntimeError, OSError, ValueError) as exc:
            # Contained like dispatch's guarded(): the lane re-routes next
            # pass; "error" is non-retriable so a persistent infra failure
            # ends the loop instead of spinning on it.
            one = RoutedOutcome(outcome.issue_id, "error", f"routing failed: {exc}")
        routed.append(one)
        if one.progressed:
            landed.append((outcome.issue_id, merge.changed_paths(repo_root, before)))
        elif lands and one.route not in ("bounced", "re-dispatch"):
            landing_blocked = True
    return _attribute_pass_couplings(repo_root, tuple(routed), collisions, landed)


def _attribute_pass_couplings(
    repo_root: Path,
    routed: tuple[RoutedOutcome, ...],
    collisions: list[tuple[str, tuple[str, ...]]],
    landed: list[tuple[str, tuple[str, ...]]],
) -> tuple[RoutedOutcome, ...]:
    """Record this pass's missed couplings, once the whole pass is known (D9).

    The edge outlives the pass — it changes every later decomposition — so nothing
    about the pass's own ordering may decide it (basicly-kjc5.32). Attributing at
    the bounce did: a collided lane could only be blamed on the landings that
    happened to precede it, so with the lanes' completion order reversed the same
    plan taught the graph the opposite edge, or none at all. Here the inputs are
    the whole pass's landings and the declared scopes behind them — both order-free
    — and :func:`merge.record_coupling` writes the pair in a canonical direction so
    the edge is literally identical either way.

    The bounced lanes' details are completed here for the same reason: which lane
    to name is not known while the pass is still running — and so is the brief
    each bounced lane's next dispatch reads (:func:`_record_bounce_briefs`).

    Best-effort like every tracker read on the landing path: a tracker that will
    not answer costs the graph an edge, never the pass.
    """
    if not collisions:
        return routed
    try:
        attributed = merge.record_pass_couplings(
            repo_root, collisions, [bead for bead, _ in landed]
        )
    except RuntimeError, OSError, ValueError:
        # The brief is still owed: the conflicting paths are the lane's own
        # evidence and do not depend on the attribution succeeding.
        attributed = {}
    _record_bounce_briefs(repo_root, collisions, attributed)
    return tuple(_reporting_couplings(one, attributed.get(one.issue_id, ())) for one in routed)


def _record_bounce_briefs(
    repo_root: Path,
    collisions: list[tuple[str, tuple[str, ...]]],
    attributed: dict[str, tuple[str, ...]],
) -> None:
    """Tell each bounced lane's *next* dispatch what its landing conflicted on (D6).

    Without this a re-dispatched bounce says nothing new. ``bounced`` is
    retriable and deliberately not carried forward, so the lane does get a fresh
    agent — but :func:`build_bundle` assembles its prompt from the loop's fixed
    dispatch prompt plus the records published against it, and the bounce
    published none. The agent was handed the prompt it had already satisfied for
    work already committed on its branch, changed nothing, and the next landing
    re-derived the identical conflict; the second attempt escalated having
    learned nothing (basicly-bdd4, observed three times on 2026-08-05/06).

    So the brief is the same ``kind=coupling`` channel :func:`_preempt_lane`
    already uses for the collision the supervisor *predicts* — the collision it
    *observes* simply never got it. It names the conflicting paths and both
    sides, which is what turns the re-dispatch into a resolvable task rather
    than a replay.

    Published here rather than at the bounce for the D9 reason the coupling edge
    is: the culprits are not known while the pass is still running, so naming
    whoever had landed by then would leak pass order into a durable record. With
    no attribution the paths still stand on their own.

    Best-effort per record: a tracker that will not take one brief must not cost
    the other lanes theirs, nor the pass.
    """
    for bead, conflicts in collisions:
        paths = ", ".join(conflicts) or "paths git did not name"
        culprits = attributed.get(bead, ())
        who = ", ".join(culprits) if culprits else "another lane"
        try:
            record_found_info(
                repo_root,
                bead,
                FoundInfo(
                    kind="coupling",
                    summary=(
                        f"{paths}: this branch no longer rebases onto its base, because "
                        f"{who} landed over those paths"
                    ),
                    detail=(
                        "your side is the commits already on this lane's branch; the "
                        "other side is those paths as they now stand on the base. "
                        "Resolve each conflicting path against both sides and commit on "
                        "this branch — the work itself is done, do not redo it."
                    ),
                    affects=(bead,),
                ),
            )
        except RuntimeError, OSError, ValueError:
            continue


def _reporting_couplings(one: RoutedOutcome, culprits: tuple[str, ...]) -> RoutedOutcome:
    """*one* with the culprits it was attributed against named in its detail."""
    if not culprits:
        return one
    return RoutedOutcome(
        one.issue_id, one.route, f"{one.detail}; coupling recorded on {', '.join(culprits)}"
    )


def _carried_outcomes(
    repo_root: Path,
    session: SessionState,
    carried: Iterable[str],
    outcomes: tuple[LaneOutcome, ...],
) -> tuple[LaneOutcome, ...]:
    """Landing-only outcomes for the still-eligible lanes carried into this pass.

    Eligibility is :func:`ready_lanes` membership, so a carried lane that has
    since landed, blocked on a dependency, or picked up a pending decision is
    dropped rather than landed twice; a lane that somehow also got dispatched
    this pass is left to its dispatch outcome.
    """
    wanted = frozenset(carried) - {outcome.issue_id for outcome in outcomes}
    if not wanted:
        return ()
    eligible = {lane.issue_id for lane in ready_lanes(repo_root, session)}
    return tuple(_carried_outcome(issue_id) for issue_id in sorted(wanted & eligible))


def _landing_order(repo_root: Path, outcomes: tuple[LaneOutcome, ...]) -> list[LaneOutcome]:
    """Order this pass's outcomes so a lane lands before the lanes depending on it.

    Reuses the merge queue's dependency sort (kjc5.10) on the beads in hand, so
    both landing paths agree on what "topo order" means. Non-green outcomes ride
    along in the same sort — they do not land, so their position only affects
    reporting — and an unreadable tracker degrades to the arrival order.
    """
    by_id = {outcome.issue_id: outcome for outcome in outcomes}
    items = [(outcome.issue_id, outcome.issue_id) for outcome in outcomes]
    return [by_id[bead] for _, bead in merge.landing_order(repo_root, items)]


def _is_green(outcome: LaneOutcome) -> bool:
    if not outcome.dispatched:
        # A carried lane never ran this pass; its work is committed and was
        # already green when it was held (basicly-kjc5.18).
        return True
    result = outcome.result
    return (
        result is not None
        and result.executed
        and result.returncode == 0
        and not result.timed_out
        and not result.handoff
        and outcome.needs_fact is None
    )


def _seeding_declined(
    repo_root: Path,
    session: SessionState,
    *,
    skip: frozenset[str],
    admission: policy.SpendStatus | None,
) -> tuple[RoutedOutcome, ...] | None:
    """Why this pass will not seed, or None to go ahead — :func:`seed_lanes`' guards.

    Split out so the caller keeps one return per *outcome* rather than one per
    precondition; the reasons themselves are unrelated to each other.
    """
    if ready_lanes(repo_root, session, skip=skip):
        return ()
    if not session.open_children:
        return ()
    if admission is None:
        return None
    blocked_runner = metered_without_a_budget(repo_root, admission)
    if blocked_runner is None:
        return None
    return (
        RoutedOutcome(
            session.root_issue,
            "seed-blocked",
            f"not provisioning lanes for the {blocked_runner!r} runner: "
            "no grant with a token budget covers this session",
        ),
    )


def seed_lanes(
    repo_root: Path,
    session: SessionState,
    *,
    skip: frozenset[str] = frozenset(),
    admission: policy.SpendStatus | None = None,
) -> tuple[RoutedOutcome, ...]:
    """Provision the root's child worktrees when the pass has nothing to dispatch.

    Without this, ``loop supervise <root>`` cannot start work at all. ``ready_lanes``
    returns only lanes at phase ``build``, a bead reaches ``build`` only by acquiring a
    worktree binding, and the code that provisions one — ``loop._ensure_child_worktrees``,
    reached from the root's decompose->build advance — sits on no supervise path. So a
    cold root reported "no ready lanes and nothing to land" and exited while dozens of
    dependency-unblocked children sat at ``intake``, and three handovers documented a
    command that dispatched nothing (basicly-t73d).

    Delegated to ``loop.run_until_blocked`` on the root rather than reimplemented, so the
    decompose checkpoint, the worktree cap and the ready-set filter keep their single
    definition — and an L1 grant covers that checkpoint precisely so this needs no human.

    Runs only when there is genuinely nothing to dispatch: with lanes already in flight,
    re-advancing the root would provision past what the cap intends. Termination is why
    the route depends on what was provisioned rather than on the attempt having been
    made — a root that seeds nothing dispatchable returns a non-retriable outcome, so the
    pass says why and stops rather than spinning. :func:`_seeding_outcome` decides which.

    *admission* short-circuits the whole step when the dispatch it would feed cannot
    start anyway. Provisioning is not cheap — a ``uv sync`` and an ``npm install`` per
    lane — so seeding five worktrees and then refusing the dispatch for want of a budget
    wastes minutes on a pass that was never going to run (basicly-kkux).
    """
    declined = _seeding_declined(repo_root, session, skip=skip, admission=admission)
    if declined is not None:
        return declined
    try:
        steps = loop.run_until_blocked(repo_root, session.root_issue)
    except (RuntimeError, OSError, ValueError) as exc:
        return (RoutedOutcome(session.root_issue, "error", f"seeding the root failed: {exc}"),)
    final = steps[-1] if steps else None
    if final is None:
        return ()
    return _seeding_outcome(repo_root, session, steps, final, skip=skip)


def _seeding_outcome(
    repo_root: Path,
    session: SessionState,
    steps: list[loop.AdvanceResult],
    final: loop.AdvanceResult,
    *,
    skip: frozenset[str],
) -> tuple[RoutedOutcome, ...]:
    """Route a completed seeding attempt on what it *provisioned* (basicly-jr0l.57).

    The route used to depend on whether the **root's own** advance progressed, and a
    package root parked awaiting its children can never progress — ``run_until_blocked``
    returns its steps as blocked by construction. So a pass that had just created N
    worktrees reported ``seed-blocked``, and because that route is deliberately *not*
    retriable, the session ended and discarded the lanes it had built. Observed on
    basicly-jr0l: five worktrees provisioned, then ``seed-blocked - no lane could be
    provisioned from 28 open child(ren)`` — a message false in its own terms — while the
    identical command dispatched all four fundable lanes on its second run. That second
    run is the whole tell: the state was right and only the verdict was wrong.

    Re-derived rather than read off *session*, because the entire point of seeding is
    that the bindings did not exist when this pass derived its state. Termination is
    unaffected: :func:`_seeding_declined` returns early once a pass starts with ready
    lanes, so the ``seeded`` route can be taken at most once per lane set.

    A lane set that exists but is wholly undispatchable still terminates — nothing
    another pass could change — but it says so rather than claiming nothing was built.
    """
    live_before = frozenset(lane.issue_id for lane in session.adopted if lane.live)
    derived = derive_session(repo_root, session.root_issue)
    dispatchable = ready_lanes(repo_root, derived, skip=skip)
    if any(step.progressed for step in steps) or dispatchable:
        detail = final.detail
        if dispatchable:
            detail = f"provisioned {len(dispatchable)} dispatchable lane(s) - {detail}"
        return (RoutedOutcome(session.root_issue, "seeded", detail),)
    gained = frozenset(lane.issue_id for lane in derived.adopted if lane.live) - live_before
    if gained:
        return (
            RoutedOutcome(
                session.root_issue,
                "seed-blocked",
                f"provisioned {len(gained)} lane(s) but none is dispatchable "
                f"({', '.join(sorted(gained))}) - {final.detail}",
            ),
        )
    return (
        RoutedOutcome(
            session.root_issue,
            "seed-blocked",
            f"no lane could be provisioned from {len(session.open_children)} open "
            f"child(ren) - {final.detail}",
        ),
    )


def repair_stale_bindings(repo_root: Path, session: SessionState) -> tuple[RoutedOutcome, ...]:
    """Dispose of adopted lanes whose worktree is gone; the outcomes recorded.

    ``derive_session`` already flags these ``live=False`` and its own comment says such
    a lane "needs a re-dispatch, not an adoption" — but nothing acted on it, so the
    lane was re-adopted and re-discarded on every pass while the bead sat at ``build``
    permanently, out of reach of both ``ready_lanes`` and ``advance_parked``
    (basicly-1koh). This is the step that acts.

    Safe cases are cleared silently-but-reported: with the ref gone the bead falls back
    to the phase its checkpoints evidence and the next fan-out re-provisions it, which
    is the "re-dispatch" the adoption comment always intended. An unsafe case — a branch
    still carrying unlanded commits — is enqueued as a decision instead, because
    clearing it would make those commits unreachable from the loop and re-provisioning
    would fork a second branch for one bead.

    Idempotent: a cleared binding is not adopted next pass, and an enqueued decision is
    keyed by (issue, kind, question) so a lane that keeps refusing re-reports one item.
    """
    routed: list[RoutedOutcome] = []
    for lane in session.adopted:
        if lane.live:
            continue
        clearable, detail = loop.stale_binding_verdict(repo_root, lane.binding)
        if not clearable:
            decisions.enqueue(
                repo_root,
                lane.issue_id,
                "escalation",
                "a worktree binding outlived its worktree and its branch is unlanded: "
                "merge the branch, delete it, or clear the binding?",
                detail,
            )
            routed.append(RoutedOutcome(lane.issue_id, "decision", detail))
            continue
        loop.clear_worktree_binding(repo_root, lane.issue_id)
        routed.append(RoutedOutcome(lane.issue_id, "repaired", detail))
    return tuple(routed)


def advance_parked(
    repo_root: Path, session: SessionState, *, beat: Callable[[], None] | None = None
) -> tuple[RoutedOutcome, ...]:
    """Advance lanes the engine drives without a top-level runner dispatch.

    Two shapes qualify. A lane routed ``merged`` parks in verify until its ship
    checkpoint is approved (by a human after the queued request, or by a later
    grant); once approvable, the only correct move is more ``loop.advance`` —
    never a fresh dispatch against an already-merged branch. A lane still in
    build that carries sub-task beads is a mini-loop lane (basicly-kjc5.9): its
    sub-tasks are dispatched one at a time from inside ``loop.advance``, so the
    supervisor advances it here instead of dispatching the lane bead itself.
    Lanes with a pending judgment stay parked. A lane whose worktree is gone is not
    advanced here either — it is disposed of by :func:`repair_stale_bindings` before
    the pass reaches this point (basicly-1koh).
    """
    routed: list[RoutedOutcome] = []
    for lane in session.adopted:
        if not lane.live or decisions.has_pending(repo_root, lane.issue_id):
            continue
        if beat is not None:
            beat()
        try:
            phase = _phase_of(repo_root, lane.issue_id)
            mini_loop = phase == "build" and _has_subtasks(repo_root, lane.issue_id)
            if phase not in ("verify", "ship") and not mini_loop:
                continue
            steps = loop.run_until_blocked(repo_root, lane.issue_id)
        except (RuntimeError, OSError, ValueError) as exc:
            routed.append(
                RoutedOutcome(lane.issue_id, "error", f"advancing parked lane failed: {exc}")
            )
            continue
        final = steps[-1] if steps else None
        if final is None:
            continue
        if final.to_phase == "done":
            routed.append(RoutedOutcome(lane.issue_id, "shipped", final.detail))
        elif final.to_phase == "build":
            # Still building: report whether the pass closed a sub-task (the loop
            # keeps iterating) or the lane is now waiting on an agent/human.
            progressed = any(step.progressed for step in steps)
            route = "lane-step" if progressed else "lane-blocked"
            routed.append(RoutedOutcome(lane.issue_id, route, final.detail))
        else:
            routed.append(RoutedOutcome(lane.issue_id, "merged", final.detail))
    return tuple(routed)


def _route_one(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Route one collected outcome, appending to the pass's *landed*/*collisions*.

    Both ledgers are required rather than defaulted: a caller that omitted
    *collisions* would drop a bounce's coupling silently, which is the D9
    regression this shape exists to prevent (basicly-kjc5.32).
    """
    issue_id = outcome.issue_id
    result = outcome.result
    if not outcome.dispatched:
        # Carried lane: nothing ran, so there is no run to triage — land it.
        return _land_green(repo_root, session, outcome, landed, collisions)
    if result is not None and result.handoff:
        return RoutedOutcome(issue_id, "handoff", outcome.detail)
    # Held-by-the-queue shapes come before the failure branch: a nonzero exit
    # that also wrote the sentinel is waiting on the fact, not on a retry —
    # burning a dispatch-rework attempt on it would be double jeopardy. A lane the
    # engine refused on its size is the same shape for a stronger reason
    # (basicly-jr0l.16): the refusal is deterministic arithmetic, so every retry
    # would reach the identical verdict and only delay the escalation that already
    # holds the lane.
    if outcome.refused or outcome.needs_fact is not None:
        return RoutedOutcome(issue_id, "decision", outcome.detail)
    if result is not None and result.timed_out:
        return _route_timeout(repo_root, session, outcome, landed, collisions)
    if result is None or result.returncode != 0:
        return _route_failed(repo_root, issue_id, outcome)
    return _land_green(repo_root, session, outcome, landed, collisions)


def _route_timeout(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Where a hard-killed lane goes, decided by whether its worktree was rescued.

    A kill whose worktree :func:`commit.salvage` committed lands like any other
    committed lane (basicly-yvx9). The stall item queued at dispatch already holds
    the *timeout* for a human; what routes here is the *diff*, and verify is the
    authority on a diff where a clock is not — green lands, red reworks the lane
    with real findings about the code the killed run actually wrote.

    With nothing committed the lane parks on that queue item, exactly as every
    timeout did before: there is no diff for a landing to judge, and the killed run
    is not a failure of the work that a bounded re-dispatch could fix — it would
    only reach the same clock.
    """
    if not outcome.salvaged:
        return RoutedOutcome(outcome.issue_id, "decision", outcome.detail)
    return _land_green(repo_root, session, outcome, landed, collisions)


def _route_blocked_landing(
    repo_root: Path,
    outcome: LaneOutcome,
    landing: loop.AdvanceResult,
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Where a blocked landing goes, read off the merge attempt behind it.

    The shape decides: a scope collision bounces back to the lane (and does not
    hold the pass), a rework cap already escalated into the decision queue, an
    uncommitted worktree is bounded by the dispatch cap like a failed run, and
    anything else is a plain rework block the loop's own counter bounds.
    """
    attempt = landing.landing
    if attempt is not None and attempt.conflicted:
        return _bounce_lane(repo_root, outcome.issue_id, landing, attempt, collisions)
    if landing.action == "escalated":
        # loop._rework already queued the escalation (kjc5.4); the pending item
        # now holds the lane until a human triages it.
        return RoutedOutcome(outcome.issue_id, "decision", landing.detail)
    if attempt is not None and attempt.foreign:
        # A tracker-wide gate failed on another lane's finishing record, which is
        # what makes this a supervisor's problem rather than a lane's: every lane in
        # the pass shares one `.beads` through the redirect, so the identical
        # assertion fails inside every sibling's landing. This lane is green and
        # committed and no evidence faults it, so it takes the ``held`` shape — carry
        # it forward to land first next pass, charge it nothing, and do not re-dispatch
        # an agent to rewrite a correct diff (basicly-qorx). The loop already
        # attributed the failure to the culprits and escalated it; holding here is
        # what stops the pass from spending a full verify run per remaining lane to
        # reach the same verdict.
        return RoutedOutcome(outcome.issue_id, "held", landing.detail)
    if attempt is not None and attempt.unreliable:
        # The gate failed and then passed unchanged, so this lane is green and
        # committed and only the gate was unreliable (basicly-55yh). That is
        # exactly the ``held`` shape: carry it forward to land first next pass,
        # and do not re-dispatch an agent over work no evidence faults — a fresh
        # dispatch would spend tokens rewriting a correct diff.
        return RoutedOutcome(outcome.issue_id, "held", landing.detail)
    if attempt is not None and attempt.status == "not-ready":
        # A green run that committed nothing (merge's not-ready guard,
        # basicly-4psl) would re-dispatch forever un-counted — bound it with the
        # dispatch rework cap like a failed run.
        return _route_failed(repo_root, outcome.issue_id, outcome)
    return RoutedOutcome(outcome.issue_id, "rework", landing.detail)


# The merge gate's convergence threshold, and the strictest one there is: the
# *first* repeat stops the lane, where a finding-set gate warns once first
# (:data:`policy.MAX_STALLED_REWORK_ROUNDS`). The verdict is shared; this number is
# the one thing about it that is the merge gate's own, so it is named here rather
# than assumed by whoever reads ``stalled``.
MAX_REPEAT_BOUNCES = 1


def conflict_signature(attempt: merge.MergeResult) -> tuple[str, ...]:
    """What a landing failed on, reduced to a comparable finding set (pure).

    The merge gate's members are its cause and its conflicting paths, where a
    test gate's are its failing checks — one shape, so one mechanism can store and
    compare both (basicly-m4zv.5). :func:`policy.finding_signature` sorts and
    dedupes them, because git's ordering is not a fact about the collision and two
    orderings of one conflict must not read as two different failures. The status
    is tagged rather than bare so a cause can never be mistaken for a path.

    Only the lane's own status and paths go in, so no pass ordering reaches a
    durable record (D9).
    """
    return policy.finding_signature((f"status={attempt.status}", *attempt.conflicts))


def _bounce_convergence(
    repo_root: Path, issue_id: str, attempt: merge.MergeResult
) -> policy.Convergence | None:
    """Record this bounce's signature and judge it; None when the tracker refused.

    Tolerant on purpose, exactly as the comment write and read it replaced were:
    the bounce still has a lane to brief and a coupling to attribute, and letting a
    tracker hiccup turn a routable bounce into an ``error`` route would cost more
    than the missed comparison — one lost signature delays an escalation by a
    bounce and suppresses none.
    """
    try:
        return policy.record_finding_set(
            repo_root, issue_id, merge.MERGE_GATE, conflict_signature(attempt)
        )
    except RuntimeError, OSError, ValueError:
        return None


def _bounce_lane(
    repo_root: Path,
    issue_id: str,
    landing: loop.AdvanceResult,
    attempt: merge.MergeResult,
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Bounce a collided lane back to its owner and note the collision (D5).

    The rework attempt was already recorded by the loop's own landing (and it
    escalated into the decision queue if that hit the cap); what the supervisor
    adds is the graph edge that makes the next decomposition serialize what it
    wrongly called parallel-safe. That edge is deliberately **not** written here:
    naming whoever had landed by the time this bounce happened is the pass-order
    dependence D9 forbids, so the conflicting paths are noted as evidence and
    :func:`_attribute_pass_couplings` attributes them once the pass is over
    (basicly-kjc5.32) — and publishes the brief the lane's next dispatch reads
    (:func:`_record_bounce_briefs`).

    What *is* recorded here is the failure signature, because it is order-free
    and the next bounce needs it: a landing that failed exactly as it failed last
    time escalates instead of spending another attempt
    (:func:`_escalate_repeat_bounce`). It is recorded through
    :func:`policy.record_finding_set`, which is where every gate's signature
    history lives, and the threshold below is the merge gate's own.

    There is no resolution of any kind here: the base was left untouched and the
    lane keeps its commits for its agent to re-apply on the new base.
    """
    collisions.append((issue_id, attempt.conflicts))
    convergence = _bounce_convergence(repo_root, issue_id, attempt)
    if convergence is not None and convergence.stalled_rounds >= MAX_REPEAT_BOUNCES:
        return _escalate_repeat_bounce(repo_root, issue_id, convergence)
    # At the rework cap the loop already queued the escalation, so the lane is
    # held by a pending decision rather than re-dispatched — say so.
    route = "decision" if landing.action == "escalated" else "bounced"
    return RoutedOutcome(issue_id, route, f"bounced back to the lane: {landing.detail}")


def _escalate_repeat_bounce(
    repo_root: Path, issue_id: str, convergence: policy.Convergence
) -> RoutedOutcome:
    """A landing that failed exactly as it failed last time: stop, and charge nothing.

    The strictest threshold on the shared convergence verdict, and the merge
    gate's own: it escalates on the *first* repeat where a finding-set gate warns
    and escalates on the second (:data:`policy.MAX_STALLED_REWORK_ROUNDS`). A
    repeated finding set is only probably stalled, since an agent may have changed
    something the gate does not report; re-applying one branch to one anchor
    provably cannot converge, so there is nothing a second attempt could do
    differently.

    The attempt is *refunded* rather than merely reported. The loop's landing
    already charged it (:func:`loop._rework`) before the supervisor saw the
    shape, and an attempt that could not have changed the outcome is not one the
    lane spent: on 2026-08-05 that charge was the whole remaining budget, and the
    pass ended on a human decision the first bounce had already reported verbatim.
    :func:`policy.spend_convergence_refund` offsets it additively — and only once,
    so a lane nobody answers still reaches its cap rather than bouncing forever
    forgiven.

    Deliberately blind to *why* the branch is unchanged. A second consecutive
    collision on one anchor is a decomposition the graph got wrong, and a human
    deciding that is the point of the escalation — whether the lane's agent tried
    and failed or never tried at all. The queue item is the loop's own rework
    escalation, and :func:`decisions.enqueue` is idempotent per question, so a
    landing that already escalated at the cap is not queued twice.
    """
    signature = " ".join(convergence.members)
    policy.spend_convergence_refund(repo_root, issue_id, merge.MERGE_GATE)
    item = decisions.enqueue(
        repo_root,
        issue_id,
        policy.REWORK_ESCALATION_KIND,
        policy.rework_escalation_question(merge.MERGE_GATE),
        (
            f"the landing failed identically to the previous attempt ({signature}); "
            "re-applying this branch to the same anchor cannot converge — re-scope it, "
            "serialize it, or resolve the conflict by hand"
        ),
    )
    return RoutedOutcome(
        issue_id,
        "decision",
        (
            f"bounced identically twice on {signature}; escalated without charging "
            f"rework ({item.decision_id})"
        ),
    )


def _route_failed(repo_root: Path, issue_id: str, outcome: LaneOutcome) -> RoutedOutcome:
    """A failed dispatch retries under the bounded rework cap, then escalates.

    A dispatch the *tracker's storage* lost is charged to its own counter instead
    (R7, basicly-vkh0.10). Nothing spawned and the lane's tree is untouched, so the
    dispatch budget — which exists to bound how many times an agent may be re-run at
    a problem — has no claim on it. Spending it here is what parked a lane that
    never started an agent on the 2026-08-02 five-lane pass, while br reported the
    contention as ``retryable: false`` and the supervisor believed it.
    """
    if outcome.transient:
        return _capped_dispatch(
            repo_root,
            issue_id,
            route="retry",
            detail=outcome.detail,
            question="the tracker's storage kept failing this dispatch: retry or park?",
            gate=TRACKER_GATE,
        )
    return _capped_dispatch(
        repo_root,
        issue_id,
        route="retry",
        detail=outcome.detail,
        question="dispatch failed at the rework cap: retry, re-dispatch, or park?",
    )


def _capped_dispatch(  # noqa: PLR0913 — route, detail and question vary independently
    repo_root: Path,
    issue_id: str,
    *,
    route: str,
    detail: str,
    question: str,
    gate: str = DISPATCH_GATE,
) -> RoutedOutcome:
    """Owe *issue_id* another dispatch, bounded by *gate*'s rework cap.

    One counter for every reason a lane needs re-running (a failed run, a
    pre-empted landing): the cap has to bound the *dispatches* a lane can spend,
    so splitting it per reason would let a lane alternate between them and never
    reach an escalation. At the cap the queue item holds the lane for a human.

    *gate* is the single exception, and it is not a reason the lane needs re-running
    at all: :data:`TRACKER_GATE` counts dispatches the store lost before the lane
    ran. Alternating between the two cannot postpone an escalation, because a lane
    only reaches the tracker counter by not having been dispatched.
    """
    config = policy.load_policy(repo_root)
    attempts = policy.record_rework(repo_root, issue_id, gate)
    if attempts < config.max_rework:
        return RoutedOutcome(
            issue_id, route, f"{detail} ({gate} rework {attempts}/{config.max_rework})"
        )
    item = decisions.enqueue(repo_root, issue_id, "escalation", question, detail)
    return RoutedOutcome(issue_id, "decision", f"{detail}; escalated as {item.decision_id}")


def _invalidated_by(
    repo_root: Path,
    session: SessionState,
    issue_id: str,
    landed: list[tuple[str, tuple[str, ...]]],
) -> tuple[str, ...]:
    """Lanes landed this pass that broke *issue_id*'s pending landing (D6).

    Read from the evidence, not from a proxy. ``git merge-tree`` predicts the
    merge without touching any tree (:func:`merge.probe_merge`), and the paths it
    names are intersected with what each landing changed
    (:func:`merge.missed_couplings`) to say *which* landing did it — the same
    attribution a bounce makes, one attempt earlier. A lane's declared ``##
    Scope`` is only what it promised to touch, so overlapping it does not mean
    the landing is doomed: cancelling on that would spend an agent run replacing
    work that would have landed clean.

    Nothing is returned unless a landing *this pass* is to blame. A branch that
    conflicts on its own is the bounce path's business, which records the missed
    coupling and takes the rework attempt the loop's own landing owes it.

    Every way of failing to read the evidence — no adopted lane, no worktree
    record, an unreadable session — yields nothing rather than raising: the
    remedy costs an agent run, so it is spent on a demonstrated collision only,
    and a lane that would have landed must never be cancelled by a git hiccup.
    """
    if not landed:
        return ()
    lane = next((la for la in session.adopted if la.issue_id == issue_id and la.live), None)
    if lane is None:
        return ()
    try:
        record = worktree.load_session(lane.binding.name, repo_root)
        if record is None:
            return ()
        probe = merge.probe_merge(repo_root, record.base, record.branch)
    except RuntimeError, OSError, ValueError:
        return ()
    if probe.safe:
        return ()
    return merge.missed_couplings(probe.conflicts, landed)


def _preempt_lane(repo_root: Path, issue_id: str, culprits: tuple[str, ...]) -> RoutedOutcome:
    """Cancel a lane a landing this pass broke and re-dispatch it *informed* (D6).

    D6 forbids messaging a lane whose base moved under it; the write-side
    counterpart is not to let it walk into the collision either. So the landing is
    skipped and the lane owes a fresh dispatch — but a re-dispatch that says
    nothing new would run the same agent, on the same tree, to the same
    collision. What makes it worth a run is the ``kind=coupling`` found-info
    record published here: that is D6's own propagation channel, and
    :func:`build_bundle` folds it into this lane's **next** prompt, so its agent
    re-applies its intent knowing which lane landed what.

    No dependency edge is recorded either — the found-info record already carries
    the discovery to the one lane that needs it, and the graph learns the coupling
    from the bounce if the re-applied work collides again. (That edge is
    non-gating since basicly-grrb, so the choice here is about not duplicating a
    record, no longer about avoiding a stall.)

    Cancelling is not destructive: like a bounce, the lane keeps its commits on
    its branch. What it costs is a dispatch, so it is bounded — once per lane per
    pass, because a lane routes exactly once — and counted against the dispatch
    rework cap, which escalates to a human rather than re-dispatching forever.
    """
    who = ", ".join(culprits)
    record_found_info(
        repo_root,
        issue_id,
        FoundInfo(
            kind="coupling",
            summary=f"{who} landed changes this branch no longer merges cleanly onto",
            detail=(
                "the supervisor cancelled this lane's landing rather than let it "
                "collide; re-apply your intent on top of what those lanes landed"
            ),
            affects=(issue_id,),
        ),
    )
    return _capped_dispatch(
        repo_root,
        issue_id,
        route="re-dispatch",
        detail=(
            f"cancelled before landing: {who} landed changes this branch no longer "
            "merges onto; recorded for the next dispatch"
        ),
        question=(
            "a landing keeps breaking this lane's merge at the rework cap: "
            "re-scope it, serialize it, or park?"
        ),
    )


def _land_green(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
    collisions: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Land a green lane through the single-track engine, then try to ship it.

    A lane whose merge one of *landed* just broke is cancelled and re-dispatched
    instead (:func:`_preempt_lane`, D6) — the doomed landing is not attempted.
    Otherwise ``loop.advance`` does the build→verify landing (rebase, verify,
    gate) — the supervisor composes it, never replaces it. A blocked landing is
    triaged by :func:`_route_blocked_landing`, which adds a collision to
    *collisions* for the pass to attribute at the end. The ship checkpoint is
    then tried non-interactively: an L3 grant with the lights-out preconditions
    holding approves and the next advance ships; otherwise the request queues for
    the human and the lane parks in verify.
    """
    invalidated = _invalidated_by(repo_root, session, outcome.issue_id, landed)
    if invalidated:
        return _preempt_lane(repo_root, outcome.issue_id, invalidated)
    landing = loop.advance(repo_root, outcome.issue_id)
    if landing.blocked:
        return _route_blocked_landing(repo_root, outcome, landing, collisions)
    approval = policy.approve_checkpoint_guarded(
        repo_root,
        outcome.issue_id,
        "ship",
        interactive=False,
        grant_root=session.root_issue,
    )
    if approval.status != "approved":
        item = decisions.enqueue(
            repo_root,
            outcome.issue_id,
            "checkpoint",
            f"approve the ship checkpoint for {outcome.issue_id}",
            # The approval's own detail says why a grant declined, when one did
            # (basicly-5ltn) — the human answering this item is the one who needs
            # it, and the wrinkle is often in a sibling lane's bead.
            "; ".join(part for part in (landing.detail, approval.detail) if part),
        )
        return RoutedOutcome(
            outcome.issue_id,
            "merged",
            f"landed; ship awaits a human ({item.decision_id})",
        )
    shipped = loop.run_until_blocked(repo_root, outcome.issue_id)
    final = shipped[-1] if shipped else landing
    if final.to_phase == "done":
        return RoutedOutcome(outcome.issue_id, "shipped", final.detail)
    return RoutedOutcome(outcome.issue_id, "merged", final.detail)

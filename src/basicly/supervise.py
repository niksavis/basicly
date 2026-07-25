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

import hashlib
import json
import os
import secrets
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from . import (
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
from .config import (
    AUTONOMY_LEVELS,
    SizingConfig,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)

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
        """Ids of the session's children that are not closed."""
        return tuple(cid for cid, status in self.children if status != "closed")

    @property
    def done(self) -> bool:
        """True when the session's work is finished (root closed, or no open child)."""
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

    @property
    def supervised(self) -> bool:
        """True when a live supervisor is bound to this session's root."""
        return self.holder is not None and self.holder_on_this_root and not self.holder_stale


def observe(repo_root: Path, root_issue: str) -> Observation:
    """Snapshot the session a client just attached to — a pure read (design 7.3).

    Layer 3's status half. It is the same derivation the supervisor runs on
    every tick (:func:`derive_session`), plus the four facts a client cannot get
    from the tracker alone: who holds the lock and how fresh their heartbeat is,
    what each in-flight lane last ran, and how much of the grant's token budget
    (D3) the session has spent.

    Takes no lock and writes nothing, so any number of clients may attach while
    the supervisor works — and attaching to an *unsupervised* root is a valid
    read, not an error: ``holder`` is then None.
    """
    state = derive_session(repo_root, root_issue)
    holder = read_holder(repo_root)
    grant = policy.active_grant(repo_root, root_issue)
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
        spent_tokens=policy.session_spend_tokens(repo_root, root_issue),
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
    body = (
        f"Continues {issue_id}: its run crossed the context ceiling "
        f"({occupancy} >= {ceiling} tokens), so the lane finalized early (factory design "
        "D8/7.6). Check which acceptance criteria the partial landing already satisfied "
        "before redoing work.\n\n"
        f"## Acceptance Criteria\n\n{acceptance}\n\n## Scope\n\n{scope_lines}\n"
    )
    issue_type = record.get("issue_type")
    if issue_type not in ("bug", "chore", "task"):
        issue_type = "task"
    create_args = ["create", title, "-t", str(issue_type), "-d", body, "--json"]
    if root_issue != issue_id:
        create_args[3:3] = ["--parent", root_issue]
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
    """
    blocked = set(loop_state.blocked_ids(repo_root))
    ranks = {node.issue_id: node.rank for node in loop_state.ready_ranked(repo_root)}
    live = [
        lane
        for lane in session.adopted
        if lane.live
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
    """
    return decisions.enqueue(
        repo_root,
        root_issue,
        "escalation",
        "re-grant autonomy or continue by hand: the session's token budget is spent",
        admission.detail,
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
    (basicly-kjc5.23). A halted session starts nothing new — in-flight lanes
    still land through the routing layer — and the halt is enqueued on the root
    so the human learns that re-granting is required. *admission* lets a caller
    that already read the status pass it in; omitting it re-reads here, so no
    dispatch path can bypass the ceiling by forgetting to check.

    *skip* excludes lanes the caller lands without a runner (basicly-kjc5.18).
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

    def guarded(lane: AdoptedLane) -> LaneOutcome:
        # Per-lane containment: a transient br failure (e.g. a locked tracker
        # DB under this very concurrency) or an OS hiccup in one lane must not
        # discard every other lane's outcome at collection time.
        try:
            return _dispatch_lane(repo_root, session, lane, spec, sizing)
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
            )

    pool = ThreadPoolExecutor(max_workers=max(1, cap))
    try:
        futures = [pool.submit(guarded, lane) for lane in lanes]
        pending = set(futures)
        while pending:
            _done, pending = wait(pending, timeout=HEARTBEAT_INTERVAL_S if beat else None)
            if pending and beat is not None:
                beat()
    except BaseException:
        pool.shutdown(wait=False, cancel_futures=True)
        raise
    pool.shutdown(wait=True)
    return tuple(future.result() for future in futures)


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


def flag_stalled_lane(
    repo_root: Path, issue_id: str, stall_after: float, runner_timeout: float
) -> decisions.DecisionItem:
    """Queue a lane as possibly-stuck, leaving the run to continue (design section 6).

    Idempotent per (issue, kind, question), so a lane is flagged once however many
    times it is sampled. The item names the hard kill deliberately: the human's
    real choice is whether to intervene now or let the timeout arrive.
    """
    return decisions.enqueue(
        repo_root,
        issue_id,
        "stall",
        "lane may be stuck: intervene now or let the hard kill arrive?",
        # :g rather than :.0f — a sub-second stall_after (tests, tight configs)
        # otherwise reads as "0s", which says the opposite of what happened.
        f"no commits and no file changes for {stall_after:g}s; the run continues "
        f"until runner_timeout ({runner_timeout:g}s), still holding a lane slot",
    )


def _dispatch_lane(
    repo_root: Path,
    session: SessionState,
    lane: AdoptedLane,
    spec: runner.RunnerSpec,
    sizing: SizingConfig,
) -> LaneOutcome:
    """Run one lane: assemble its bundle now, dispatch, record, and meter."""
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
    known = frozenset({session.root_issue, *(cid for cid, _ in session.children)})
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
    loop.record_run(
        repo_root,
        lane.issue_id,
        spec,
        result,
        prompt=bundle.prompt,
        phase="lane",
        folded_info=tuple(_folded_ref(info) for info in bundle.folded),
    )
    if result.timed_out:
        # Consume any sentinel the killed run managed to write — leaving it
        # would mis-attribute the fact to the *next* dispatch after triage.
        stale_needs = needs_input.take(cwd)
        # Hard-kill stall (design section 6): route to the decision queue and
        # hold the lane until a human (or the decider) triages it.
        stall = decisions.enqueue(
            repo_root,
            lane.issue_id,
            "stall",
            f"runner {spec.name} hit runner_timeout "
            f"({runner_config.runner_timeout:.0f}s): retry, re-dispatch, or park?",
            stale_needs.fact if stale_needs is not None else "",
        )
        return LaneOutcome(
            issue_id=lane.issue_id,
            runner_name=spec.name,
            result=result,
            needs_fact=None,
            occupancy=None,
            overrun=False,
            followup_id=None,
            detail=f"timed out after {runner_config.runner_timeout:.0f}s; "
            f"stall queued as {stall.decision_id}",
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
    occupancy = runner.context_occupancy(spec, result)
    ceiling = ceiling_tokens(spec, sizing)
    overrun = occupancy is not None and occupancy >= ceiling
    # The follow-up is tied to a coherent partial landing (design 7.6): a run
    # that failed or stopped on a missing fact lands nothing, gets re-dispatched
    # by the routing layer, and must not pin a premature remainder bead through
    # the idempotence marker.
    followup_id = (
        finalize_followup(
            repo_root,
            session.root_issue,
            lane.issue_id,
            occupancy=occupancy or 0,
            ceiling=ceiling,
        )
        if overrun and result.returncode == 0 and needs is None
        else None
    )
    if result.returncode != 0:
        detail = f"runner exited {result.returncode}"
    elif needs is not None:
        detail = f"needs input: {needs.detail or needs.fact}"
    elif overrun:
        detail = f"finished but crossed the context ceiling; remainder in {followup_id}"
    else:
        detail = "finished; ready to land"
    return LaneOutcome(
        issue_id=lane.issue_id,
        runner_name=spec.name,
        result=result,
        needs_fact=needs.fact if needs is not None else None,
        occupancy=occupancy,
        overrun=overrun,
        followup_id=followup_id,
        detail=detail,
    )


# --- Outcome routing: green lands, everything else queues (basicly-kjc5.7) ---


# Rework gate name for failed dispatches: bounded like merge/verify rework, so
# a crash-looping runner escalates to the queue instead of retrying forever.
DISPATCH_GATE = "dispatch"


# Routes that keep the standing loop iterating even without a landing: the
# lane will be re-tried and its termination is bounded elsewhere (the dispatch
# and verify rework caps both escalate into the decision queue, which then
# holds the lane via has_pending). "lane-step" is a mini-loop lane that closed a
# sub-task this pass — bounded by max_subtasks_per_lane plus those same caps;
# "bounced" is a collided lane whose agent re-applies its intent next pass,
# bounded by the same merge rework cap. "lane-blocked" is deliberately absent,
# because such a lane waits on an agent or a human exactly like a handoff.
RETRIABLE_ROUTES = ("retry", "rework", "held", "lane-step", "bounced")


@dataclass(frozen=True)
class RoutedOutcome:
    """Where one lane's outcome went after collection (design component 5)."""

    issue_id: str
    # "shipped" | "merged" | "retry" | "rework" | "held" | "decision"
    # | "handoff" | "lane-step" | "lane-blocked" | "bounced" | "error"
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
      going: the missed coupling is recorded as a dependency edge on whichever
      lane landed the colliding paths this pass, and the remaining green lanes
      still land. A lane's wrong scope declaration is its own problem, not a
      reason to stall everyone.
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
    covers queues a checkpoint request for the human. *beat* fires between
    outcomes; per-outcome failures are contained to that lane's route so one br
    hiccup cannot discard the rest of the pass. Outcomes are returned in the
    order they were processed (landing order), not in the order they came in.
    """
    routed: list[RoutedOutcome] = []
    landing_blocked = False
    # (bead, paths its landing added to the base) per landing this pass — the
    # evidence a collision is attributed against (merge.missed_couplings).
    landed: list[tuple[str, tuple[str, ...]]] = []
    pass_outcomes = _carried_outcomes(repo_root, session, carried, outcomes) + outcomes
    for outcome in _landing_order(repo_root, pass_outcomes):
        if beat is not None:
            beat()
        is_green = _is_green(outcome)
        if landing_blocked and is_green:
            routed.append(
                RoutedOutcome(
                    outcome.issue_id,
                    "held",
                    "landing paused after an earlier failure this pass",
                )
            )
            continue
        before = merge.head_sha(repo_root) if is_green else ""
        try:
            one = _route_one(repo_root, session, outcome, landed)
        except (RuntimeError, OSError, ValueError) as exc:
            # Contained like dispatch's guarded(): the lane re-routes next
            # pass; "error" is non-retriable so a persistent infra failure
            # ends the loop instead of spinning on it.
            one = RoutedOutcome(outcome.issue_id, "error", f"routing failed: {exc}")
        routed.append(one)
        if one.progressed:
            landed.append((outcome.issue_id, merge.changed_paths(repo_root, before)))
        elif is_green and one.route != "bounced":
            landing_blocked = True
    return tuple(routed)


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
    Lanes with a pending judgment stay parked.
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
    landed: list[tuple[str, tuple[str, ...]]] | None = None,
) -> RoutedOutcome:
    issue_id = outcome.issue_id
    result = outcome.result
    if not outcome.dispatched:
        # Carried lane: nothing ran, so there is no run to triage — land it.
        return _land_green(repo_root, session, outcome, landed or [])
    if result is not None and result.handoff:
        return RoutedOutcome(issue_id, "handoff", outcome.detail)
    # Held-by-the-queue shapes come before the failure branch: a nonzero exit
    # that also wrote the sentinel is waiting on the fact, not on a retry —
    # burning a dispatch-rework attempt on it would be double jeopardy.
    if (result is not None and result.timed_out) or outcome.needs_fact is not None:
        return RoutedOutcome(issue_id, "decision", outcome.detail)
    if result is None or result.returncode != 0:
        return _route_failed(repo_root, issue_id, outcome)
    return _land_green(repo_root, session, outcome, landed or [])


def _route_blocked_landing(
    repo_root: Path,
    outcome: LaneOutcome,
    landing: loop.AdvanceResult,
    landed: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Where a blocked landing goes, read off the merge attempt behind it.

    The shape decides: a scope collision bounces back to the lane (and does not
    hold the pass), a rework cap already escalated into the decision queue, an
    uncommitted worktree is bounded by the dispatch cap like a failed run, and
    anything else is a plain rework block the loop's own counter bounds.
    """
    attempt = landing.landing
    if attempt is not None and attempt.conflicted:
        return _bounce_lane(repo_root, outcome.issue_id, landing, attempt, landed)
    if landing.action == "escalated":
        # loop._rework already queued the escalation (kjc5.4); the pending item
        # now holds the lane until a human triages it.
        return RoutedOutcome(outcome.issue_id, "decision", landing.detail)
    if attempt is not None and attempt.status == "not-ready":
        # A green run that committed nothing (merge's not-ready guard,
        # basicly-4psl) would re-dispatch forever un-counted — bound it with the
        # dispatch rework cap like a failed run.
        return _route_failed(repo_root, outcome.issue_id, outcome)
    return RoutedOutcome(outcome.issue_id, "rework", landing.detail)


def _bounce_lane(
    repo_root: Path,
    issue_id: str,
    landing: loop.AdvanceResult,
    attempt: merge.MergeResult,
    landed: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Bounce a collided lane back to its owner and record the missed coupling (D5).

    The rework attempt was already recorded by the loop's own landing (and it
    escalated into the decision queue if that hit the cap); what the supervisor
    adds is the graph edge — the lane that landed the colliding paths this pass —
    so the next decomposition serializes what it wrongly called parallel-safe.
    There is no resolution of any kind here: the base was left untouched and the
    lane keeps its commits for its agent to re-apply on the new base.
    """
    couplings = merge.missed_couplings(attempt.conflicts, landed)
    for culprit in couplings:
        merge.record_coupling(repo_root, issue_id, culprit)
    detail = f"bounced back to the lane: {landing.detail}"
    if couplings:
        detail += f"; coupling recorded on {', '.join(couplings)}"
    # At the rework cap the loop already queued the escalation, so the lane is
    # held by a pending decision rather than re-dispatched — say so.
    route = "decision" if landing.action == "escalated" else "bounced"
    return RoutedOutcome(issue_id, route, detail)


def _route_failed(repo_root: Path, issue_id: str, outcome: LaneOutcome) -> RoutedOutcome:
    """A failed dispatch retries under the bounded rework cap, then escalates."""
    config = policy.load_policy(repo_root)
    attempts = policy.record_rework(repo_root, issue_id, DISPATCH_GATE)
    if attempts < config.max_rework:
        return RoutedOutcome(
            issue_id,
            "retry",
            f"{outcome.detail} (dispatch rework {attempts}/{config.max_rework})",
        )
    item = decisions.enqueue(
        repo_root,
        issue_id,
        "escalation",
        f"dispatch failed {attempts} time(s) at the rework cap: retry, re-dispatch, or park?",
        outcome.detail,
    )
    return RoutedOutcome(issue_id, "decision", f"{outcome.detail}; escalated as {item.decision_id}")


def _land_green(
    repo_root: Path,
    session: SessionState,
    outcome: LaneOutcome,
    landed: list[tuple[str, tuple[str, ...]]],
) -> RoutedOutcome:
    """Land a green lane through the single-track engine, then try to ship it.

    ``loop.advance`` does the build→verify landing (rebase, verify, gate) — the
    supervisor composes it, never replaces it. A blocked landing is triaged by
    :func:`_route_blocked_landing` against *landed* (this pass's landings, which
    a collision is attributed against). The ship checkpoint is then tried
    non-interactively: an L3 grant with the lights-out preconditions holding
    approves and the next advance ships; otherwise the request queues for the
    human and the lane parks in verify.
    """
    landing = loop.advance(repo_root, outcome.issue_id)
    if landing.blocked:
        return _route_blocked_landing(repo_root, outcome, landing, landed)
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
            landing.detail,
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

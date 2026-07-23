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

import json
import os
import secrets
import time
from collections.abc import Callable, Iterable
from concurrent.futures import ThreadPoolExecutor, wait
from dataclasses import dataclass
from pathlib import Path

from . import decisions, decompose, loop, loop_state, needs_input, policy, runner, worktree
from .br import run_br as _run_br
from .config import (
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
    return DispatchBundle(issue_id=issue_id, prompt=prompt, folded=folded)


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


def ready_lanes(repo_root: Path, session: SessionState) -> tuple[AdoptedLane, ...]:
    """The session's dispatchable lanes: adopted, live, and unblocked per ``br``.

    Readiness is re-checked at pass time, because a dependency edge added since
    provisioning (e.g. a found-info coupling) must gate the lane *now*. The gate
    is blocked-ness, not ready-list membership: a provisioned lane is claimed
    (in_progress), and ``br scheduler`` recommends only unclaimed work — so the
    scheduler's rank orders the lanes it does know, and the rest follow in
    adoption order.
    """
    blocked = set(loop_state.blocked_ids(repo_root))
    ranks = {node.issue_id: node.rank for node in loop_state.ready_ranked(repo_root)}
    live = [lane for lane in session.adopted if lane.live and lane.issue_id not in blocked]
    return tuple(
        sorted(live, key=lambda lane: (ranks.get(lane.issue_id, float("inf")), lane.issue_id))
    )


def dispatch_lanes(
    repo_root: Path,
    session: SessionState,
    *,
    beat: Callable[[], None] | None = None,
    cap: int | None = None,
    dispatch_one: Callable[..., LaneOutcome] | None = None,
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
    """
    lanes = ready_lanes(repo_root, session)
    if not lanes:
        return ()
    if cap is None:
        cap = load_worktree_config(repo_root).concurrency
    if dispatch_one is None:
        dispatch_one = _dispatch_lane
    config = load_runner_config(repo_root)
    spec = runner.select_runner(config.specs, config.default, capable=runner.is_capable)
    sizing = load_sizing_config(repo_root)

    def guarded(lane: AdoptedLane) -> LaneOutcome:
        # Per-lane containment: a transient br failure (e.g. a locked tracker
        # DB under this very concurrency) or an OS hiccup in one lane must not
        # discard every other lane's outcome at collection time.
        try:
            return dispatch_one(repo_root, session, lane, spec, sizing)
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
    result = runner.run(spec, bundle.prompt, cwd, capture_usage=True)
    loop.record_run(repo_root, lane.issue_id, spec, result)
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

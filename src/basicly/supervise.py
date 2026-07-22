"""Supervisor core: singleton lock, session lifecycle, crash recovery (basicly-kjc5.5).

Factory design D1/7.2: one deterministic supervisor process per repo owns the
base checkout, the machine concurrency budget, and the single-writer usage
files — so supervisor-ness itself must be a singleton. This module is part 1
of component 5: the lock, the session definition, and crash recovery. The
standing event loop that composes dispatch and the merge queue (kjc5.6) builds
on these primitives; ``basicly loop supervise`` currently runs one derivation
pass under the lock and reports what it adopted.

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
from dataclasses import dataclass
from pathlib import Path

from . import loop_state, worktree
from .br import run_br as _run_br

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


def _binding_of(
    repo_root: Path, issue_id: str, record: dict | None
) -> loop_state.WorktreeBinding | None:
    """The issue's worktree binding, reading ``br`` unless *record* is at hand."""
    if record is None:
        proc = _run_br(repo_root, ["show", issue_id, "--json"])
        data = json.loads(proc.stdout)
        record = data[0] if isinstance(data, list) else data
        if not isinstance(record, dict):
            return None
    return loop_state.parse_worktree_ref(record.get("external_ref"))

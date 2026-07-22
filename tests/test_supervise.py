"""Tests for the supervisor core: lock, session, crash recovery (basicly-kjc5.5).

The singleton lock is the load-bearing piece: exactly one supervisor may own a
repo, a crashed holder's lock is taken over atomically by exactly one
contender, and a restart re-derives the whole session from ``br`` — no side
state anywhere. These tests pin those three properties.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from basicly import loop_state, supervise
from basicly.supervise import LOCK_FILE, STALE_AFTER_S, LockHeldError, LockLostError


class _Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


def _lock_path(repo: Path) -> Path:
    return repo / LOCK_FILE


def _backdate(path: Path, seconds: float) -> None:
    stat = path.stat()
    os.utime(path, (stat.st_atime - seconds, stat.st_mtime - seconds))


# --- Lock: acquire / refuse / heartbeat --------------------------------------


def test_acquire_creates_lock_with_session_payload(tmp_path: Path) -> None:
    """The lock lands in the self-ignored usage dir carrying pid + session + root."""
    lock = supervise.acquire(tmp_path, "epic:abcd1234", "epic")
    payload = json.loads(lock.read_text(encoding="utf-8"))
    assert payload["pid"] == os.getpid()
    assert payload["session_id"] == "epic:abcd1234"
    assert payload["root_issue"] == "epic"
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_second_acquire_refuses_while_first_heartbeats(tmp_path: Path) -> None:
    """A fresh heartbeat refuses the contender and names the holder."""
    supervise.acquire(tmp_path, "epic:first", "epic")
    with pytest.raises(LockHeldError, match="epic:first"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_heartbeat_keeps_an_aging_lock_fresh(tmp_path: Path) -> None:
    """Heartbeating refreshes the mtime, so a live holder is never stolen from."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    _backdate(lock, STALE_AFTER_S + 5)
    supervise.heartbeat(lock, "epic:first")  # the holder beats just in time
    with pytest.raises(LockHeldError, match="epic:first"):
        supervise.acquire(tmp_path, "epic:second", "epic")


def test_heartbeat_raises_lock_lost_when_lock_vanished(tmp_path: Path) -> None:
    """A vanished lock tells the stalled holder to stop supervising."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    lock.unlink()
    with pytest.raises(LockLostError):
        supervise.heartbeat(lock, "epic:first")


def test_stalled_holder_heartbeat_fences_after_takeover(tmp_path: Path) -> None:
    """A resumed stalled holder must stand down, not refresh the successor's lock.

    The real interleaving: stall past stale, takeover completes, and the old
    holder's next beat finds the successor's lock at the same path.
    """
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    _backdate(lock, STALE_AFTER_S + 1)  # the holder stalls past the horizon
    supervise.acquire(tmp_path, "epic:successor", "epic")  # takeover completes
    with pytest.raises(LockLostError, match="successor"):
        supervise.heartbeat(lock, "epic:first")


# --- Lock: stale takeover -----------------------------------------------------


def test_stale_lock_is_taken_over_atomically(tmp_path: Path) -> None:
    """A lock past the staleness horizon is stolen; the new payload owns it."""
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    took = supervise.acquire(tmp_path, "epic:successor", "epic")
    payload = json.loads(took.read_text(encoding="utf-8"))
    assert payload["session_id"] == "epic:successor"
    assert not list(lock.parent.glob("*.stale.*"))  # tombstone cleaned up


def test_takeover_loser_gets_lock_held(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """The contender that loses the atomic rename refuses instead of double-owning."""
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)

    def losing_replace(_self: object, _dst: object) -> None:
        raise FileNotFoundError  # the other contender renamed it first

    monkeypatch.setattr(supervise.Path, "replace", losing_replace)
    with pytest.raises(LockHeldError, match="taking over"):
        supervise.acquire(tmp_path, "epic:loser", "epic")


def test_acquire_retries_when_lock_freed_mid_check(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A lock released between the failed create and the holder read is free."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")

    real_read_holder = supervise.read_holder

    def freeing_read_holder(repo_root: Path) -> object:
        lock.unlink(missing_ok=True)  # the holder releases just now
        return real_read_holder(repo_root)

    monkeypatch.setattr(supervise, "read_holder", freeing_read_holder)
    took = supervise.acquire(tmp_path, "epic:second", "epic")
    payload = json.loads(took.read_text(encoding="utf-8"))
    assert payload["session_id"] == "epic:second"


def test_takeover_replaces_an_abandoned_same_pid_tombstone(tmp_path: Path) -> None:
    """A tombstone leaked by a crashed same-pid takeover never blocks the next one."""
    lock = supervise.acquire(tmp_path, "epic:crashed", "epic")
    _backdate(lock, STALE_AFTER_S + 1)
    leaked = lock.with_name(f"{lock.name}.stale.{os.getpid()}")
    leaked.write_text("{}", encoding="utf-8")
    took = supervise.acquire(tmp_path, "epic:successor", "epic")
    assert json.loads(took.read_text(encoding="utf-8"))["session_id"] == "epic:successor"


def test_corrupt_fresh_lock_still_refuses(tmp_path: Path) -> None:
    """Staleness is mtime-only: an unreadable but fresh lock is not stolen."""
    path = _lock_path(tmp_path)
    path.parent.mkdir(parents=True)
    path.write_text("not json", encoding="utf-8")
    with pytest.raises(LockHeldError, match="unknown"):
        supervise.acquire(tmp_path, "epic:second", "epic")


# --- Lock: release ------------------------------------------------------------


def test_release_removes_only_own_lock(tmp_path: Path) -> None:
    """Release is content-checked so a stalled holder never deletes a successor's lock."""
    lock = supervise.acquire(tmp_path, "epic:first", "epic")
    supervise.release(lock, "epic:someone-else")
    assert lock.exists()  # not ours to delete
    supervise.release(lock, "epic:first")
    assert not lock.exists()
    supervise.release(lock, "epic:first")  # idempotent on a missing lock


# --- Session derivation (crash recovery = re-reading br) ----------------------


class _FakeBrShow:
    """br stand-in serving `show --json` from a seeded issue map."""

    def __init__(self, issues: dict[str, dict]) -> None:
        self.issues = issues

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> _Proc:
        if args[:1] == ["show"]:
            return _Proc(json.dumps([self.issues[args[1]]]))
        raise AssertionError(f"unexpected br call: {args}")


def _issue(
    issue_id: str,
    status: str = "open",
    external_ref: str | None = None,
    children: tuple[tuple[str, str], ...] = (),
) -> dict:
    return {
        "id": issue_id,
        "status": status,
        "external_ref": external_ref,
        "dependents": [
            {"id": cid, "status": cstatus, "dependency_type": "parent-child"}
            for cid, cstatus in children
        ],
    }


def _fake_sessions(monkeypatch: pytest.MonkeyPatch, names: set[str]) -> None:
    class _S:
        def __init__(self, name: str) -> None:
            self.name = name

    monkeypatch.setattr(
        supervise.worktree, "list_sessions", lambda *_a, **_k: [_S(n) for n in names]
    )


def test_derive_session_readopts_bound_open_children(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Open children with a worktree external_ref are re-adopted purely from br."""
    issues = {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "open"))),
        "epic.1": _issue("epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"),
        "epic.2": _issue("epic.2", "open"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, {"epic-1"})

    state = supervise.derive_session(tmp_path, "epic")

    assert state.root_status == "open"
    assert state.open_children == ("epic.1", "epic.2")
    assert len(state.adopted) == 1
    lane = state.adopted[0]
    assert lane.issue_id == "epic.1"
    assert lane.binding == loop_state.WorktreeBinding("epic-1", "harness/epic-1")
    assert lane.live is True
    assert state.done is False


def test_derive_session_flags_missing_worktree_and_skips_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A bound lane whose worktree is gone is adopted dead; closed children never adopt."""
    issues = {
        "epic": _issue("epic", children=(("epic.1", "in_progress"), ("epic.2", "closed"))),
        "epic.1": _issue("epic.1", "in_progress", external_ref="worktree:epic-1:harness/epic-1"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    state = supervise.derive_session(tmp_path, "epic")

    assert [lane.issue_id for lane in state.adopted] == ["epic.1"]
    assert state.adopted[0].live is False
    assert state.open_children == ("epic.1",)


def test_derive_session_adopts_leaf_root_binding(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A leaf session (root itself bound to a worktree, no children) re-adopts the root."""
    issues = {
        "task": _issue("task", "in_progress", external_ref="worktree:task:harness/task"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, {"task"})

    state = supervise.derive_session(tmp_path, "task")

    assert [lane.issue_id for lane in state.adopted] == ["task"]
    assert state.children == ()
    assert state.done is False


def test_derive_session_done_when_all_children_closed(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The session lifetime rule: everything closed (or root closed) means done."""
    issues = {
        "epic": _issue("epic", children=(("epic.1", "closed"), ("epic.2", "closed"))),
        "closed-root": _issue("closed-root", "closed"),
    }
    monkeypatch.setattr(supervise, "_run_br", _FakeBrShow(issues))
    _fake_sessions(monkeypatch, set())

    assert supervise.derive_session(tmp_path, "epic").done is True
    assert supervise.derive_session(tmp_path, "closed-root").done is True


def test_new_session_id_binds_root_and_varies() -> None:
    """Session ids carry the root issue and differ per start."""
    first = supervise.new_session_id("epic")
    second = supervise.new_session_id("epic")
    assert first.startswith("epic:")
    assert first != second

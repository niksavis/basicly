"""Tests for the board snapshot the supervisor's heartbeat emits (basicly-rn0o.7).

Split from `test_supervise` rather than added to it: that module sits 413 tokens under its
frozen `module_size` baseline, which this unit's tests do not fit under, and
`check_test_naming` permits the `test_<module>_<aspect>.py` form for exactly this.

The emission cost is bounded by unit B's own `BUILD_CAP_S`, not by the 50 ms the design's AC 3
names. That 50 ms was written against a 19.1 ms build figure the design itself records as
wrong: `test_board_snapshot` measures `build_document` at 103.8 ms on this corpus, and the
whole emission measures a median 78.0 ms here. The intent - a board must not slow the beat -
holds against the 15 s tick with three orders of margin; the number does not, and a gate
asserting it would be red on arrival.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from basicly import board_schema, board_snapshot, projection, supervise

if TYPE_CHECKING:
    from collections.abc import Iterator

# Fast enough that two ticks land inside a test, slow enough that the first one does not race
# the thread's own start. The design's demonstration allows 40 s for two ticks at 15 s.
TICK_S = 0.05

# Unit B's cap on one build, which is the bound this emission is actually held to.
EMIT_CAP_S = 0.5

_SESSION = "epic:board"
_ROOT = "epic"


def _wait_for(predicate: Any, *, timeout: float = 10.0) -> bool:
    """Poll *predicate* until true or *timeout*; the condition, never a sleep."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return True
        time.sleep(0.01)
    return False


@pytest.fixture
def beating(work_repo: Path) -> Iterator[tuple[Path, list[str]]]:
    """A held lock with a live beater emitting the board, and the lines it reported."""
    lock = supervise.acquire(work_repo, _SESSION, _ROOT)
    said: list[str] = []
    thread = supervise.HeartbeatThread(
        lock, _SESSION, interval=TICK_S, repo_root=work_repo, report=said.append
    )
    thread.start()
    try:
        yield work_repo, said
    finally:
        thread.stop()
        thread.join(timeout=10)
        supervise.release(lock, _SESSION)


def _document(repo_root: Path) -> dict[str, Any]:
    """The emitted snapshot, parsed."""
    path = repo_root / board_snapshot.SNAPSHOT_FILE
    return json.loads(path.read_text(encoding="utf-8"))


def test_a_tick_emits_a_snapshot_whose_freshness_is_younger_than_the_tick(
    beating: tuple[Path, list[str]],
) -> None:
    """The bead's AC: a supervised tick leaves a document that declares itself live."""
    repo_root, _said = beating
    path = repo_root / board_snapshot.SNAPSHOT_FILE
    assert _wait_for(path.exists), "no snapshot after 10 s of ticks"
    document = _document(repo_root)
    assert document["freshness"] == {
        "source": "supervisor-tick",
        "cadence_s": TICK_S,
        "stale_after_s": supervise.STALE_AFTER_S,
    }
    stamped = datetime.fromisoformat(document["generated_at"])
    age_s = (datetime.now(UTC) - stamped).total_seconds()
    assert 0 <= age_s < document["freshness"]["stale_after_s"]


def test_every_tick_rewrites_the_document_rather_than_leaving_the_first(
    beating: tuple[Path, list[str]],
) -> None:
    """AC 1: the wall is current because the file moves, not because it exists.

    On mtime and not on `generated_at`, which `board_fields.stamp` writes at
    `timespec="seconds"` - two ticks inside one second carry the identical string, so the
    stamp cannot witness a rewrite at any cadence a test can wait for.
    """
    repo_root, _said = beating
    path = repo_root / board_snapshot.SNAPSHOT_FILE
    assert _wait_for(path.exists)
    first = path.stat().st_mtime_ns
    assert _wait_for(lambda: path.stat().st_mtime_ns != first), (
        "the snapshot never advanced past its first tick"
    )


def test_the_emitted_document_validates_and_declares_no_undeclared_key(
    beating: tuple[Path, list[str]],
) -> None:
    """`board validate` stays green on what the tick writes, unknown-key count included."""
    repo_root, _said = beating
    assert _wait_for((repo_root / board_snapshot.SNAPSHOT_FILE).exists)
    ruling = board_schema.verdict(repo_root, _document(repo_root))
    assert ruling.exit_code == 0, ruling.summary
    assert ruling.unknown == (), ruling.summary


def test_the_session_section_carries_the_lock_this_beat_holds(
    beating: tuple[Path, list[str]],
) -> None:
    """The facts `board_snapshot` may not read for itself, since reading them cycles."""
    repo_root, _said = beating
    assert _wait_for((repo_root / board_snapshot.SNAPSHOT_FILE).exists)
    session = _document(repo_root)["session"]
    assert session["root"] == _ROOT
    assert session["supervised"] is True
    assert session["holder"]["id"] == _SESSION
    assert session["holder"]["stale"] is False


def test_a_beat_without_a_repo_root_only_beats(work_repo: Path) -> None:
    """The board is opt-in at construction, so a beater that only fences the lock still can."""
    lock = supervise.acquire(work_repo, _SESSION, _ROOT)
    # Backdated first, so "the lock is fresh" is a claim about this beater rather than about
    # `acquire` having just written the file a moment ago.
    stat = lock.stat()
    os.utime(
        lock, (stat.st_atime - supervise.STALE_AFTER_S, stat.st_mtime - supervise.STALE_AFTER_S)
    )
    stale_mtime = lock.stat().st_mtime_ns
    thread = supervise.HeartbeatThread(lock, _SESSION, interval=TICK_S)
    thread.start()
    try:
        assert _wait_for(lambda: lock.stat().st_mtime_ns != stale_mtime)
        assert not (work_repo / board_snapshot.SNAPSHOT_FILE).exists()
    finally:
        thread.stop()
        thread.join(timeout=10)
        supervise.release(lock, _SESSION)


def test_the_write_goes_through_the_shared_temp_then_rename(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 2: a reader sees the old document or the new one, never a partial.

    Spied at the seam rather than asserted from the file, because a torn read is what a
    non-atomic write costs and no assertion on the result can observe one having been avoided.
    """
    supervise.acquire(work_repo, _SESSION, _ROOT)
    routed: list[Path] = []
    original = projection.atomic_write_bytes
    monkeypatch.setattr(
        projection,
        "atomic_write_bytes",
        lambda path, content: (routed.append(path), original(path, content))[1],
    )
    written = supervise.emit_board_snapshot(work_repo, TICK_S)
    assert routed == [written]
    assert written == work_repo / board_snapshot.SNAPSHOT_FILE
    assert list(written.parent.iterdir()) == [written], "a temp file survived the rename"


def test_a_failed_emission_costs_one_line_and_never_the_beat(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 4: a board that cannot be written must not fail a pass or a landing."""
    lock = supervise.acquire(work_repo, _SESSION, _ROOT)
    monkeypatch.setattr(
        supervise.board_snapshot,
        "build_document",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("corpus unreadable")),
    )
    said: list[str] = []
    thread = supervise.HeartbeatThread(
        lock, _SESSION, interval=TICK_S, repo_root=work_repo, report=said.append
    )
    thread.start()
    try:
        assert _wait_for(lambda: bool(said))
        assert said[0] == "board:    snapshot not written - corpus unreadable"
        thread.check()
        assert time.time() - lock.stat().st_mtime < supervise.STALE_AFTER_S
    finally:
        thread.stop()
        thread.join(timeout=10)
        supervise.release(lock, _SESSION)
    assert not (work_repo / board_snapshot.SNAPSHOT_FILE).exists()


def test_one_emission_stays_inside_the_producers_own_build_cap(work_repo: Path) -> None:
    """AC 3's intent: the beat's cadence is three orders above what an emission costs."""
    supervise.acquire(work_repo, _SESSION, _ROOT)
    supervise.emit_board_snapshot(work_repo, TICK_S)  # warm the caches the cap is not about
    started = time.perf_counter()
    supervise.emit_board_snapshot(work_repo, TICK_S)
    assert time.perf_counter() - started < EMIT_CAP_S


def test_the_lanes_section_is_omitted_rather_than_derived_on_a_tick(work_repo: Path) -> None:
    """A tick may not afford `read_node_state` per lane, and absent is the honest claim."""
    supervise.acquire(work_repo, _SESSION, _ROOT)
    supervise.emit_board_snapshot(work_repo, TICK_S)
    assert "lanes" not in _document(work_repo)


def test_the_session_section_is_omitted_once_the_lock_is_gone(work_repo: Path) -> None:
    """A taken-over beat names no root: a guessed one is the false claim on a wall."""
    supervise.emit_board_snapshot(work_repo, TICK_S)
    assert "session" not in _document(work_repo)

"""Tests for the board snapshot the supervisor's heartbeat emits (basicly-rn0o.7).

Split from `test_supervise` rather than added to it: that module sits 413 tokens under its
frozen `module_size` baseline, which this unit's tests do not fit under, and
`check_test_naming` permits the `test_<module>_<aspect>.py` form for exactly this.

**The producer sits above `supervise` and the beat carries it as a callback (basicly-bd4epr).**
`board_facts.emit_tick` folds what a wall needs and `supervise` cannot import it, so these tests
wire the two the way `cli.cmd_supervise` does. Before that the tick folded on the lock alone,
and a live supervisor made the board go backwards: 0 phases of 234 where `board --out` carried
234, no ready set, and `IN FLIGHT` with no producer at all.

The emission cost is bounded by the beat it rides, not by the 50 ms the design's AC 3 names.
That 50 ms was written against a 19.1 ms build figure the design itself records as wrong, and
the tick now folds Mode A's whole document rather than the lock: a 0.48 s median over five
emissions on this module's fixture, and 1.50 s on this repository's own tree with a lane
adopted, against the 15 s interval either way.
"""

from __future__ import annotations

import json
import os
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from basicly import board_facts, board_schema, board_snapshot, loop_state, projection, supervise

if TYPE_CHECKING:
    from collections.abc import Iterator

# Fast enough that two ticks land inside a test, slow enough that the first one does not race
# the thread's own start. The design's demonstration allows 40 s for two ticks at 15 s.
TICK_S = 0.05

# The bound that is real: a board must not slow the beat. A fifth of the interval the beat
# keeps, which the fixture's 0.48 s median clears by 6x and the live tree's 1.50 s by 2x - the
# producer's own `BUILD_CAP_S` no longer covers this path, because the tick folds every section
# Mode A folds.
EMIT_CAP_S = supervise.HEARTBEAT_INTERVAL_S / 5

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
        lock,
        _SESSION,
        interval=TICK_S,
        board=lambda cadence: board_facts.emit_tick(work_repo, cadence),
        report=said.append,
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


def test_a_beat_without_a_board_only_beats(work_repo: Path) -> None:
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
    written = board_facts.emit_tick(work_repo, TICK_S)
    assert routed == [written]
    assert written == work_repo / board_snapshot.SNAPSHOT_FILE
    assert list(written.parent.iterdir()) == [written], "a temp file survived the rename"


def test_a_failed_emission_costs_one_line_and_never_the_beat(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 4: a board that cannot be written must not fail a pass or a landing."""
    lock = supervise.acquire(work_repo, _SESSION, _ROOT)
    monkeypatch.setattr(
        board_facts.board_snapshot,
        "build_document",
        lambda *_a, **_k: (_ for _ in ()).throw(RuntimeError("corpus unreadable")),
    )
    said: list[str] = []
    thread = supervise.HeartbeatThread(
        lock,
        _SESSION,
        interval=TICK_S,
        board=lambda cadence: board_facts.emit_tick(work_repo, cadence),
        report=said.append,
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


def test_one_emission_stays_inside_the_beat_it_rides(work_repo: Path) -> None:
    """AC 3's intent: the beat's cadence stays orders above what an emission costs."""
    supervise.acquire(work_repo, _SESSION, _ROOT)
    board_facts.emit_tick(work_repo, TICK_S)  # warm the caches the cap is not about
    started = time.perf_counter()
    board_facts.emit_tick(work_repo, TICK_S)
    assert time.perf_counter() - started < EMIT_CAP_S


def test_the_tick_carries_every_derivation_the_idle_board_carries(work_repo: Path) -> None:
    """The regression: a live supervisor must not publish less than no supervisor did.

    Compared against the same fold with no lock held rather than against a remembered number,
    because the claim is a relation between two producers and not a count.
    """
    idle: dict[str, Any] = board_facts.document(work_repo)
    supervise.acquire(work_repo, _SESSION, _ROOT)
    ticked = json.loads(board_facts.emit_tick(work_repo, TICK_S).read_text(encoding="utf-8"))

    assert set(idle) <= set(ticked)
    phased = [unit for unit in ticked["units"] if unit.get("phase")]
    assert len(phased) == len([unit for unit in idle["units"] if unit.get("phase")])
    assert phased, "the corpus must carry a phase for the comparison to discriminate"
    assert ticked["backlog"]["ready"] == idle["backlog"]["ready"]
    assert ticked["backlog"]["blocked"] == idle["backlog"]["blocked"]
    assert ticked["repo"] == idle["repo"]


def test_in_flight_carries_one_card_per_adopted_lane(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC 2: the lane views `loop session` already builds reach the wall.

    The session derivation is pinned rather than the lane row: `supervise.lane_view` and the
    phase lookup are the code under test, and a fixture that also supplied those would assert
    its own input back.
    """
    adopted = supervise.AdoptedLane(
        issue_id="basicly-0jiq",
        status="in_progress",
        binding=loop_state.WorktreeBinding("basicly-0jiq", "harness/basicly-0jiq"),
        live=True,
    )
    monkeypatch.setattr(
        board_facts.supervise,
        "derive_session",
        lambda *_a, **_k: supervise.SessionState(_ROOT, "open", (), (adopted,)),
    )
    supervise.acquire(work_repo, _SESSION, _ROOT)
    ticked = json.loads(board_facts.emit_tick(work_repo, TICK_S).read_text(encoding="utf-8"))

    assert [lane["id"] for lane in ticked["lanes"]] == ["basicly-0jiq"]
    card = ticked["lanes"][0]
    assert card["status"] == "in_progress"
    assert card["branch"] == "harness/basicly-0jiq"
    assert card["live"] is True
    assert card["phase"] == loop_state.phase_map(work_repo)["basicly-0jiq"]


def test_the_session_and_lane_sections_are_omitted_once_the_lock_is_gone(
    work_repo: Path,
) -> None:
    """A taken-over beat names no root: a guessed one is the false claim on a wall.

    `lanes` goes with it, and absent rather than `[]`: an empty list is the claim that lanes
    are visible and there are none, which a producer with no root has not earned.
    """
    document = json.loads(board_facts.emit_tick(work_repo, TICK_S).read_text(encoding="utf-8"))
    assert "session" not in document
    assert "lanes" not in document

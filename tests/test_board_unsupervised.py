"""Lanes the board must name when no supervisor holds the lock (basicly-kqh9dj8).

Against the produced rows rather than against the reader, because the defect was never that a
state was computed wrongly - every state already existed. `board_facts` returned `()` before
any of them could be assigned, so a board with four worktrees on disk said `no pass is running`.
A test that only checked `state_for` would have passed throughout that.
"""

from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from basicly import board_sections, board_unsupervised, supervise

if TYPE_CHECKING:
    from collections.abc import Sequence

FRESH = board_unsupervised.FRESH_AFTER_S

# The instant a document would be dated with; every call injects one rather than reading a clock.
MOMENT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _detail(record: str, worktree: str, branch: str = "") -> board_sections.DetailFacts:
    return board_sections.DetailFacts(id=record, worktree=worktree, branch=branch or f"h/{record}")


def _repo(tmp_path: Path) -> Path:
    """A git repo with one commit on `main`, so `rev-list main..HEAD` has something to answer."""
    root = tmp_path / "base"
    root.mkdir()

    def run(*args: str) -> None:
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)

    run("init", "-b", "main")
    run("config", "user.email", "t@example.invalid")
    run("config", "user.name", "t")
    (root / "seed.txt").write_text("seed\n")
    run("add", "-A")
    run("commit", "-m", "seed")
    return root


def _worktree(root: Path, name: str) -> Path:
    path = root.parent / name
    subprocess.run(
        ["git", "worktree", "add", "-b", f"h/{name}", str(path)],
        cwd=root,
        check=True,
        capture_output=True,
    )
    return path


def test_a_binding_with_no_lock_is_named_which_is_the_whole_defect(tmp_path: Path) -> None:
    """The board said nothing was running with worktrees on disk. This is that, inverted."""
    root = _repo(tmp_path)
    _worktree(root, "lane-one")
    rows = board_unsupervised.lanes(
        root,
        [_detail("basicly-a", "lane-one")],
        {"basicly-a": "build"},
        {"basicly-a": "open"},
        MOMENT,
    )
    assert [row.id for row in rows] == ["basicly-a"]
    assert rows[0].phase == "build"
    assert rows[0].branch == "h/basicly-a"
    assert rows[0].provisioned is True


def test_a_binding_whose_worktree_git_no_longer_tracks_draws_no_row(tmp_path: Path) -> None:
    """A record's ref outlives its directory; a row for a torn-down worktree is the inverse bug."""
    root = _repo(tmp_path)
    rows = board_unsupervised.lanes(
        root, [_detail("basicly-a", "gone")], {"basicly-a": "build"}, {"basicly-a": "open"}, MOMENT
    )
    assert rows == ()


def test_live_is_never_claimed_because_this_producer_cannot_observe_it(tmp_path: Path) -> None:
    """`provisioned` is evidenced by the directory; `live` needs a stream nobody registered."""
    root = _repo(tmp_path)
    _worktree(root, "lane-one")
    rows = board_unsupervised.lanes(
        root, [_detail("basicly-a", "lane-one")], {}, {"basicly-a": "open"}, MOMENT
    )
    assert rows[0].live is None, "an unsupervised producer cannot know an agent is inside"


@pytest.mark.parametrize(
    ("changed", "fresh", "ahead", "status", "expected"),
    [
        ((), False, 0, "deferred", supervise.LANE_PARKED),
        (("a.py",), True, 0, "deferred", supervise.LANE_PARKED),
        (("a.py",), True, 0, "open", supervise.LANE_RUNNING),
        (("a.py",), False, 0, "open", supervise.LANE_PARKED),
        ((), False, 3, "open", supervise.LANE_WAITS_TO_LAND),
        ((), False, 0, "open", supervise.LANE_QUEUED),
    ],
)
def test_each_state_has_an_observation_that_produces_it(
    changed: Sequence[str], fresh: bool, ahead: int, status: str, expected: str
) -> None:
    """A parked record outranks its tree; recency splits an agent inside from a tree left open."""
    assert board_unsupervised.state_for(changed, fresh, ahead, status)[0] == expected


def test_a_dirty_worktree_nobody_has_touched_is_not_a_running_pass() -> None:
    """basicly-ze0po3's rule, and the reason `FRESH_AFTER_S` exists.

    The `basicly-fiow1sr` worktree held nine staged files for a day after its lane was killed.
    Dirty-means-running drew it as a live agent, which is exactly the false positive the board
    is being fixed to stop making.
    """
    stale, _ = board_unsupervised.state_for(("a.py",) * 9, False, 0, "open")
    live, _ = board_unsupervised.state_for(("a.py",) * 9, True, 0, "open")
    assert stale != supervise.LANE_RUNNING
    assert live == supervise.LANE_RUNNING


def test_recency_reads_the_files_git_says_differ(tmp_path: Path) -> None:
    """Against the filesystem, because an mtime helper that never opens a path always says no."""
    root = _repo(tmp_path)
    (root / "touched.py").write_text("x\n")
    stat = (root / "touched.py").stat()
    assert board_unsupervised.touched_within(root, ["touched.py"], stat.st_mtime + 1.0, FRESH)
    assert not board_unsupervised.touched_within(
        root, ["touched.py"], stat.st_mtime + FRESH + 1.0, FRESH
    )
    # A path git names and the filesystem no longer holds is a change that already happened.
    assert not board_unsupervised.touched_within(root, ["deleted.py"], stat.st_mtime, FRESH)


def test_every_lane_state_this_producer_can_reach_is_in_the_shipped_vocabulary() -> None:
    """The gate: a state spelled here and not in `LANE_STATES` renders as nothing at all.

    `basicly-ncday7` shipped six states and one branch made all of them unreachable outside a
    supervised pass. This asserts the second spelling never diverges from the first.
    """
    reachable = {
        board_unsupervised.state_for(changed, fresh, ahead, status)[0]
        for changed in ((), ("a.py",))
        for fresh in (True, False)
        for ahead in (0, 3)
        for status in ("open", "deferred")
    }
    assert reachable <= board_sections.LANE_STATES
    assert supervise.LANE_RUNNING in reachable, "no observation produces a running lane"
    assert supervise.LANE_WAITS_TO_LAND in reachable, "no observation produces a landable lane"


def test_a_directory_that_is_not_a_repository_reports_no_lanes(tmp_path: Path) -> None:
    """The guard, and it is not hypothetical.

    `board_facts.document` is built over temporary directories that are not git repositories in
    nineteen tests across ten modules. An unguarded `git worktree list --porcelain` raised
    `command failed (128)` in every one of them - a lane producer took down the docs-claims and
    comment-density suites. A checkout that cannot answer has no lanes, which is the same claim
    as no worktrees.
    """
    assert (
        board_unsupervised.lanes(tmp_path, [_detail("basicly-a", "lane-one")], {}, {}, MOMENT) == ()
    )

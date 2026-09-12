from __future__ import annotations

import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from basicly import board_regions, board_sections, board_unsupervised, supervise

if TYPE_CHECKING:
    from collections.abc import Sequence

FRESH = board_unsupervised.FRESH_AFTER_S

MOMENT = datetime(2026, 9, 6, 12, 0, tzinfo=UTC)


def _detail(record: str, worktree: str, branch: str = "") -> board_sections.DetailFacts:
    return board_sections.DetailFacts(id=record, worktree=worktree, branch=branch or f"h/{record}")


def _repo(tmp_path: Path) -> Path:
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
    root = _repo(tmp_path)
    rows = board_unsupervised.lanes(
        root, [_detail("basicly-a", "gone")], {"basicly-a": "build"}, {"basicly-a": "open"}, MOMENT
    )
    assert rows == ()


def test_live_is_never_claimed_because_this_producer_cannot_observe_it(tmp_path: Path) -> None:
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
    assert board_unsupervised.state_for(changed, fresh, ahead, status, "build")[0] == expected


def test_zero_commits_reads_differently_past_build_than_before_it() -> None:

    merged_state, merged_why = board_unsupervised.state_for((), False, 0, "open", "verify")
    fresh_state, fresh_why = board_unsupervised.state_for((), False, 0, "open", "build")
    assert merged_why != fresh_why
    assert merged_why == board_unsupervised.MERGED_AWAITING_TEARDOWN
    assert "no commits" in fresh_why, "a worktree before build really has done nothing yet"
    assert merged_state == supervise.LANE_LANDED
    assert fresh_state == supervise.LANE_QUEUED
    assert merged_state != fresh_state, (
        "a detail under one badge is not a distinction a reader sees"
    )


@pytest.mark.parametrize("phase", ["", "intake", "classify", "decompose", "build"])
def test_a_phase_at_or_before_build_keeps_the_present_wording(phase: str) -> None:
    assert board_unsupervised.state_for((), False, 0, "open", phase)[1] == (
        "a worktree with no commits and no changes"
    )


@pytest.mark.parametrize("phase", ["verify", "validate", "ship", "done"])
def test_every_phase_after_build_reads_as_merged(phase: str) -> None:
    assert board_unsupervised.state_for((), False, 0, "open", phase)[1] == (
        board_unsupervised.MERGED_AWAITING_TEARDOWN
    )


def test_git_refusing_to_answer_is_not_read_as_a_merge() -> None:

    assert board_unsupervised.state_for((), False, None, "open", "verify")[1] == (
        "a worktree with no commits and no changes"
    )


def test_a_merged_worktree_still_on_disk_draws_a_row_that_says_so(tmp_path: Path) -> None:

    root = _repo(tmp_path)
    path = _worktree(root, "lane-one")
    (path / "landed.py").write_text("x = 1\n")
    subprocess.run(["git", "add", "-A"], cwd=path, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "work"], cwd=path, check=True, capture_output=True)
    subprocess.run(
        ["git", "merge", "--no-ff", "-m", "land", "h/lane-one"],
        cwd=root,
        check=True,
        capture_output=True,
    )
    rows = board_unsupervised.lanes(
        root,
        [_detail("basicly-a", "lane-one")],
        {"basicly-a": "verify"},
        {"basicly-a": "open"},
        MOMENT,
    )
    assert rows[0].state_detail == board_unsupervised.MERGED_AWAITING_TEARDOWN


def test_an_untouched_worktree_before_build_still_reads_as_empty(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    _worktree(root, "lane-one")
    rows = board_unsupervised.lanes(
        root,
        [_detail("basicly-a", "lane-one")],
        {"basicly-a": "build"},
        {"basicly-a": "open"},
        MOMENT,
    )
    assert rows[0].state_detail == "a worktree with no commits and no changes"


def test_a_dirty_worktree_nobody_has_touched_is_not_a_running_pass() -> None:

    stale, _ = board_unsupervised.state_for(("a.py",) * 9, False, 0, "open", "build")
    live, _ = board_unsupervised.state_for(("a.py",) * 9, True, 0, "open", "build")
    assert stale != supervise.LANE_RUNNING
    assert live == supervise.LANE_RUNNING


def test_recency_reads_the_files_git_says_differ(tmp_path: Path) -> None:
    root = _repo(tmp_path)
    (root / "touched.py").write_text("x\n")
    stat = (root / "touched.py").stat()
    assert board_unsupervised.touched_within(root, ["touched.py"], stat.st_mtime + 1.0, FRESH)
    assert not board_unsupervised.touched_within(
        root, ["touched.py"], stat.st_mtime + FRESH + 1.0, FRESH
    )
    assert not board_unsupervised.touched_within(root, ["deleted.py"], stat.st_mtime, FRESH)


def test_every_lane_state_this_producer_can_reach_is_in_the_shipped_vocabulary() -> None:

    reachable = {
        board_unsupervised.state_for(changed, fresh, ahead, status, phase)[0]
        for changed in ((), ("a.py",))
        for fresh in (True, False)
        for ahead in (0, 3)
        for status in ("open", "deferred")
        for phase in ("build", "verify")
    }
    assert reachable <= board_sections.LANE_STATES
    assert supervise.LANE_RUNNING in reachable, "no observation produces a running lane"
    assert supervise.LANE_WAITS_TO_LAND in reachable, "no observation produces a landable lane"


def test_a_directory_that_is_not_a_repository_reports_no_lanes(tmp_path: Path) -> None:

    assert (
        board_unsupervised.lanes(tmp_path, [_detail("basicly-a", "lane-one")], {}, {}, MOMENT) == ()
    )


def test_the_landed_state_is_in_the_closed_set_and_the_consumer_can_colour_it() -> None:

    assert supervise.LANE_LANDED in board_sections.LANE_STATES
    assert supervise.LANE_LANDED in board_regions.LANE_MARKS
    palette, word = board_regions.LANE_MARKS[supervise.LANE_LANDED]
    assert word == "landed"
    assert palette != board_regions.LANE_MARKS[supervise.LANE_QUEUED][0], (
        "landed and queued sharing a colour is the defect this state was added to end"
    )

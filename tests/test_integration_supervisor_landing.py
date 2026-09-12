from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path
from typing import cast

import pytest

from basicly import cli, loop, loop_state, merge, policy, repair_brief, supervise, tracker, worktree
from tests import flipped_tracker

_BASE_CONFIG = f"""\
[worktree]
base_branch = "main"
concurrency = 4

[[verify.checks]]
name = "always-green"
command = [{Path(sys.executable).as_posix()!r}, "-c", "pass"]
modes = ["fast", "full"]

[policy]
required_gates = ["verify"]
max_rework = 2
autonomy = "L2"

[policy.sizing]
# The fixture's files are tiny; without this floor the sizing governor refuses
# every plan as under-scoped before a child is ever created.
working_set_min = 1

[runner]
default = "manual"
"""

_ROOT = "fx-1"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout


def _commit(cwd: Path, path: str, body: str, message: str) -> None:
    (cwd / path).write_text(body, encoding="utf-8")
    _git(cwd, "add", "--", path)
    _git(cwd, "commit", "-m", message)


def _create_bead(repo: Path, title: str, *, issue_type: str = "task", parent: str = _ROOT) -> str:
    return tracker.create_record(
        repo,
        [
            "create",
            title,
            "-t",
            issue_type,
            "-d",
            f"## Trigger\n\nWhen the fixture dispatches {title}, I want it to land, "
            f"so I can assert the lane closed.\n\n"
            f"## Acceptance Criteria\n\n- Given the fixture when {title} then it lands\n",
            "--parent",
            parent,
            "--json",
        ],
    )


@pytest.fixture
def harness_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Harness Test")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "basicly.toml").write_text(_BASE_CONFIG, encoding="utf-8")
    (repo / "app.txt").write_text("start\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: seed the fixture repo")

    flipped_tracker.flipped_repo(repo)
    flipped_tracker.seed(repo, _ROOT, title="the fixture root", issue_type="epic")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: initialize the beads workspace")
    return repo


def _to_build(repo: Path, issue_id: str) -> loop.AdvanceResult:
    intake = loop.advance(repo, issue_id, inputs=loop.Inputs(work_type="task"))
    assert intake.checkpoint == "classify", intake.detail
    policy.approve_checkpoint(repo, issue_id, "classify")
    return loop.advance(repo, issue_id)


def _committed_lane(
    repo: Path, root_title: str, lane_title: str
) -> tuple[str, str, worktree.Session]:
    root = _create_bead(repo, root_title, issue_type="epic")
    child = _create_bead(repo, lane_title, parent=root)
    _to_build(repo, child)
    state = loop_state.read_node_state(repo, child)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    _commit(Path(session.worktree_path), "done.txt", "work\n", f"feat: the lane ({child})")
    return root, child, session


class _StubHeartbeat:
    def check(self) -> None:
        pass

    def stop(self) -> None:
        pass


def test_a_pass_lands_a_hand_committed_lane_without_dispatching_it(
    harness_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = harness_repo
    root, child, _lane = _committed_lane(repo, "the crashed session root", "the committed lane")
    monkeypatch.setattr(
        supervise, "_dispatch_lane", lambda *_a, **_k: pytest.fail("the lane was re-dispatched")
    )
    say: list[str] = []

    session_state = supervise.derive_session(repo, root)
    assert supervise.committed_lanes(repo, session_state) == frozenset({child})
    cli._supervise_rounds(
        repo,
        argparse.Namespace(issue=root, label=None, max_passes=1),
        hb=cast("supervise.HeartbeatThread", _StubHeartbeat()),
        say=say.append,
        session_id=f"{root}:0001",
    )

    assert f"routed:   {child} -> merged" in "\n".join(say), say
    assert (repo / "done.txt").exists()
    assert loop_state.read_node_state(repo, child).gates.required_passed == ("verify",)


def test_a_lane_briefed_for_repair_is_dispatched_rather_than_landed(harness_repo: Path) -> None:

    repo = harness_repo
    root, child, lane = _committed_lane(repo, "the reworking root", "the lane a gate refused")
    session_state = supervise.derive_session(repo, root)
    assert supervise.committed_lanes(repo, session_state) == frozenset({child})

    assert repair_brief.write_repair_brief(
        lane.path,
        repair_brief.RepairBrief(
            issue_id=child,
            gate="verify",
            reason="the sentinel check failed",
            branch_head=merge.branch_head(repo, lane.branch) or "",
        ),
    )

    assert supervise.committed_lanes(repo, session_state) == frozenset()


def test_a_lane_with_nothing_committed_or_a_dirty_tree_is_left_to_dispatch(
    harness_repo: Path,
) -> None:
    repo = harness_repo
    root = _create_bead(repo, "the dirty root", issue_type="epic")
    child = _create_bead(repo, "the lane still working", parent=root)
    _to_build(repo, child)
    state = loop_state.read_node_state(repo, child)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    session_state = supervise.derive_session(repo, root)

    assert supervise.committed_lanes(repo, session_state) == frozenset()

    tree = Path(session.worktree_path)
    _commit(tree, "done.txt", "work\n", f"feat: the lane ({child})")
    assert supervise.committed_lanes(repo, session_state) == frozenset({child})

    (tree / "done.txt").write_text("half a thought\n", encoding="utf-8")
    assert supervise.committed_lanes(repo, session_state) == frozenset()

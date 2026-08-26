"""A pass over a lane whose work is already committed: land it, do not rebuild it.

Split out alongside ``test_integration_supervisor.py`` (a clean pass over two lanes)
and ``test_integration_supervisor_coupling.py`` (couplings found at the landing).
This third slice is the one property neither can show, because it is about what a
pass does *before* dispatching: a lane whose branch carries commits and whose tree
is clean needs the merge queue, and a pass that re-derives that from git rather
than from the last pass's routes keeps the carry across a supervisor crash
(basicly-pjaudy). The lapse is here too — a repair brief or a dirty tree puts the
lane back in the dispatcher's hands.

The recipe is ``test_integration_loop.py``'s: a fixture repository with real
git history and a real tracker workspace, driven through the engine with nothing
between it and ``git`` or the ledger. The coding agent is the one substitution, and it is
a real configuration rather than a stub — ``[runner] default = "manual"`` blocks
for its driver and the test then plays the agent by committing on the harness
branch. ``worktree.install_worktree_hooks`` is stubbed for the same reason it is
there: provisioning a repo with ``pre-commit`` is a third-party tool, not the
engine under test.
"""

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

# The fixture's root record. Every bead a test creates is a child of it, because a mint
# with no parent needs a declared `[tracker] prefix` and the fixture's config is what the
# test is *not* about (`owned_write.create`).
_ROOT = "fx-1"


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout


def _commit(cwd: Path, path: str, body: str, message: str) -> None:
    (cwd / path).write_text(body, encoding="utf-8")
    _git(cwd, "add", "--", path)
    _git(cwd, "commit", "-m", message)


def _create_bead(repo: Path, title: str, *, issue_type: str = "task", parent: str = _ROOT) -> str:
    """Create one bead carrying acceptance criteria, so the DoR gate passes."""
    return tracker.create_record(
        repo,
        [
            "create",
            title,
            "-t",
            issue_type,
            "-d",
            f"## Acceptance Criteria\n\n- Given the fixture when {title} then it lands\n",
            "--parent",
            parent,
            "--json",
        ],
    )


@pytest.fixture
def harness_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A consumer repo with real git history, a real tracker workspace, and a config."""
    # pre-commit installing the bundled hook manifest into a repo that has no
    # .pre-commit-config.yaml, over the network. Not the engine under test.
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
    """Drive intake -> classify -> a provisioned worktree, approving the checkpoint."""
    intake = loop.advance(repo, issue_id, inputs=loop.Inputs(work_type="task"))
    assert intake.checkpoint == "classify", intake.detail
    policy.approve_checkpoint(repo, issue_id, "classify")
    return loop.advance(repo, issue_id)


def _committed_lane(
    repo: Path, root_title: str, lane_title: str
) -> tuple[str, str, worktree.Session]:
    """A provisioned lane whose work is committed on its branch and whose tree is clean."""
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
    """The lock's heartbeat, reduced to the two calls a round makes of it."""

    def check(self) -> None:
        """The holder still holds the lock."""

    def stop(self) -> None:
        """Nothing to join."""


def test_a_pass_lands_a_hand_committed_lane_without_dispatching_it(
    harness_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The kjc5.18 carry is in-process, so a crash used to buy the same work twice.

    Observed 2026-08-23: two lanes sat committed and rebased on their harness
    branches after the supervisor died, and the next pass spent ~12M tokens
    re-implementing them. The reproduction is the bead's — commit the lane's work
    by hand, leave the tree clean, run a pass — and it must end in a landing with
    zero dispatches, which is what the failing ``_dispatch_lane`` asserts.
    """
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

    # It landed, through the merge queue, with no runner spent on it.
    assert f"routed:   {child} -> merged" in "\n".join(say), say
    assert (repo / "done.txt").exists()
    assert loop_state.read_node_state(repo, child).gates.required_passed == ("verify",)


def test_a_lane_briefed_for_repair_is_dispatched_rather_than_landed(harness_repo: Path) -> None:
    """A failed gate left a brief, so the commits are the defect, not the remedy.

    The carry's lapse condition: committed-and-clean is true of a lane whose
    landing just failed too, so deriving from git alone would re-land the same red
    diff every pass instead of letting the normal rework path run.
    """
    repo = harness_repo
    root, child, lane = _committed_lane(repo, "the reworking root", "the lane a gate refused")
    session_state = supervise.derive_session(repo, root)
    assert supervise.committed_lanes(repo, session_state) == frozenset({child})

    # What loop._rework leaves behind when a landing fails a repairable gate.
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
    """Landing rebases the branch, so only committed work on a clean tree qualifies."""
    repo = harness_repo
    root = _create_bead(repo, "the dirty root", issue_type="epic")
    child = _create_bead(repo, "the lane still working", parent=root)
    _to_build(repo, child)
    state = loop_state.read_node_state(repo, child)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    session_state = supervise.derive_session(repo, root)

    # Nothing committed yet: there is nothing to land, so the lane is the dispatcher's.
    assert supervise.committed_lanes(repo, session_state) == frozenset()

    tree = Path(session.worktree_path)
    _commit(tree, "done.txt", "work\n", f"feat: the lane ({child})")
    assert supervise.committed_lanes(repo, session_state) == frozenset({child})

    # ...and an uncommitted edit on top of the commit takes it back out again.
    (tree / "done.txt").write_text("half a thought\n", encoding="utf-8")
    assert supervise.committed_lanes(repo, session_state) == frozenset()

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from basicly import loop, loop_state, merge, policy, runner, supervise, tracker, worktree
from tests import flipped_tracker

SENTINEL = "BROKEN"
_PROBE = f"import pathlib,sys; sys.exit(1 if pathlib.Path({SENTINEL!r}).exists() else 0)"

_BASE_CONFIG = f"""\
[worktree]
base_branch = "main"
concurrency = 4

[[verify.checks]]
name = "sentinel"
command = [{Path(sys.executable).as_posix()!r}, "-c", {_PROBE!r}]
modes = ["fast", "full"]

[policy]
required_gates = ["verify"]
max_rework = 2
# Raised so the stand-in section can issue the L2 grant a supervisor pass needs
# to dispatch a metered runner at all. Inert for every other test here: without a
# grant on the bead, the ceiling this opts into is never reached.
autonomy = "L2"

[policy.sizing]
# The fixture's files are tiny; without this floor the sizing governor refuses
# every plan as under-scoped before a child is ever created.
working_set_min = 1
"""

_MANUAL_RUNNER_CONFIG = """
[runner]
default = "manual"
"""


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout


_ROOT = "fx-1"


def _seed_tracker(repo: Path) -> None:

    flipped_tracker.flipped_repo(repo)
    flipped_tracker.seed(repo, _ROOT, title="the fixture root", issue_type="epic")


def _tracker(cwd: Path, *args: str) -> None:

    tracker.write(cwd, list(args))


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


def _show(repo: Path, issue_id: str) -> dict:
    return tracker.read_record(repo, issue_id) or {}


def _seed_repo(tmp_path: Path, runner_config: str) -> Path:
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Harness Test")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "basicly.toml").write_text(_BASE_CONFIG + runner_config, encoding="utf-8")
    (repo / "app.txt").write_text("start\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: seed the fixture repo")

    _seed_tracker(repo)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: initialize the beads workspace")
    return repo


@pytest.fixture
def harness_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    return _seed_repo(tmp_path, _MANUAL_RUNNER_CONFIG)


def _to_build(repo: Path, issue_id: str) -> loop.AdvanceResult:
    intake = loop.advance(repo, issue_id, inputs=loop.Inputs(work_type="task"))
    assert intake.checkpoint == "classify", intake.detail
    policy.approve_checkpoint(repo, issue_id, "classify")
    return loop.advance(repo, issue_id)


def _green(issue_id: str) -> supervise.LaneOutcome:

    return supervise.LaneOutcome(
        issue_id=issue_id,
        runner_name="fixture",
        result=runner.RunResult(runner="fixture", command=(), executed=True, returncode=0),
        needs_fact=None,
        occupancy=None,
        overrun=False,
        detail="fixture dispatch",
    )


def test_a_landing_cancels_the_lane_it_broke_and_tells_it_why(harness_repo: Path) -> None:

    repo = harness_repo
    root = _create_bead(repo, "the collision root", issue_type="epic")
    first = _create_bead(repo, "lane that lands first", parent=root)
    second = _create_bead(repo, "lane that gets cancelled", parent=root)

    for name, child in (("first", first), ("second", second)):
        _to_build(repo, child)
        state = loop_state.read_node_state(repo, child)
        assert state.worktree is not None
        session = worktree.load_session(state.worktree.name, repo)
        assert session is not None
        _commit(
            Path(session.worktree_path),
            "shared.txt",
            f"the {name} lane wrote this\n",
            f"feat: lane {name} ({child})",
        )

    session_state = supervise.derive_session(repo, root)
    routed = supervise.route_outcomes(repo, session_state, (_green(first), _green(second)))

    assert [r.route for r in routed] == ["merged", "re-dispatch"], [r.detail for r in routed]
    assert first in routed[1].detail
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "the first lane wrote this\n"
    merges = [
        line
        for line in _git(repo, "log", "--format=%s", "--first-parent", "main").splitlines()
        if line.startswith("chore(worktree):")
    ]
    assert len(merges) == 1

    blocking = {
        str(dep["id"])
        for dep in _show(repo, second).get("dependencies") or []
        if dep.get("dependency_type") == "blocks"
    }
    assert blocking == set(), f"{second} was gated instead of re-dispatched: {blocking}"

    bundle = supervise.build_bundle(repo, second, known_ids=frozenset({root, first, second}))
    assert [info.kind for info in bundle.folded] == ["coupling"]
    assert first in bundle.prompt


def test_a_missed_coupling_teaches_the_graph_without_gating_the_bounced_lane(
    harness_repo: Path,
) -> None:

    repo = harness_repo
    root = _create_bead(repo, "the coupling-edge root", issue_type="epic")
    landed = _create_bead(repo, "the lane that landed", parent=root)
    bounced = _create_bead(repo, "the lane that bounced", parent=root)
    for child in (landed, bounced):
        _to_build(repo, child)

    merge.record_coupling(repo, bounced, landed)
    assert _show(repo, landed)["status"] != "closed"

    lower, higher = sorted((bounced, landed))
    coupled = {
        str(dep["id"]): dep.get("dependency_type")
        for dep in _show(repo, lower).get("dependencies") or []
    }
    assert coupled.get(higher) == merge.COUPLING_DEP_TYPE

    assert bounced not in loop_state.blocked_ids(repo)
    session_state = supervise.derive_session(repo, root)
    ready = {lane.issue_id for lane in supervise.ready_lanes(repo, session_state)}
    assert bounced in ready, f"{bounced} was gated by the coupling it taught the graph"


def test_a_discovered_coupling_gates_and_orders_the_bead_it_names(harness_repo: Path) -> None:

    repo = harness_repo
    root = _create_bead(repo, "the discovery root", issue_type="epic")
    finder = _create_bead(repo, "the lane that discovers", parent=root)
    named = _create_bead(repo, "the bead it names", parent=root)
    _to_build(repo, finder)

    supervise.record_found_info(
        repo,
        finder,
        supervise.FoundInfo(
            kind="coupling",
            summary="the config loader is shared with the runner window",
            affects=(named,),
        ),
    )

    session_state = supervise.derive_session(repo, root)
    recorded = supervise.propose_coupling_edges(repo, session_state)
    assert recorded == ((named, finder, "blocks"),)

    assert named in loop_state.blocked_ids(repo)
    ordered = merge.landing_order(repo, [(finder, finder), (named, named)])
    assert [bead for _name, bead in ordered] == [finder, named]

    assert supervise.propose_coupling_edges(repo, supervise.derive_session(repo, root)) == ()

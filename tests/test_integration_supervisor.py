from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from basicly import loop, loop_state, merge, policy, runner, supervise, tracker, worktree
from basicly.config import load_policy_config
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
            "## Trigger\n\nWhen run, I want it to land, so I can assert it.\n\n"
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


def test_a_supervisor_pass_lands_two_lanes_in_dependency_order(harness_repo: Path) -> None:

    repo = harness_repo
    root = _create_bead(repo, "the session root", issue_type="epic")
    first = _create_bead(repo, "the earlier lane", parent=root)
    second = _create_bead(repo, "the later lane", parent=root)
    _tracker(repo, "dep", "add", second, first, "-t", "blocks")

    trees = {}
    for name, child in (("first", first), ("second", second)):
        _to_build(repo, child)
        state = loop_state.read_node_state(repo, child)
        assert state.worktree is not None
        session = worktree.load_session(state.worktree.name, repo)
        assert session is not None
        trees[name] = Path(session.worktree_path)
        _commit(trees[name], f"{name}.txt", "done\n", f"feat: the {name} lane ({child})")

    session_state = supervise.derive_session(repo, root)
    assert {cid for cid, _ in session_state.children} == {first, second}
    assert {lane.issue_id for lane in session_state.adopted} == {first, second}

    routed = supervise.route_outcomes(repo, session_state, (_green(second), _green(first)))
    assert [r.issue_id for r in routed] == [first, second]
    assert [r.route for r in routed] == ["merged", "merged"], [r.detail for r in routed]

    assert (repo / "first.txt").exists()
    assert (repo / "second.txt").exists()
    subjects = _git(repo, "log", "--format=%s", "--first-parent", "main").splitlines()
    merges = [s for s in subjects if s.startswith("chore(worktree):")]
    assert len(merges) == 2

    for child in (first, second):
        assert loop_state.read_node_state(repo, child).gates.required_passed == ("verify",)


def test_a_landing_pass_invents_no_coupling_from_the_shared_tracker(harness_repo: Path) -> None:

    repo = harness_repo
    root = _create_bead(repo, "the coupling root", issue_type="epic")
    first = _create_bead(repo, "lane alpha", parent=root)
    second = _create_bead(repo, "lane beta", parent=root)

    for name, child in (("alpha", first), ("beta", second)):
        _to_build(repo, child)
        state = loop_state.read_node_state(repo, child)
        assert state.worktree is not None
        session = worktree.load_session(state.worktree.name, repo)
        assert session is not None
        _commit(
            Path(session.worktree_path),
            f"{name}.txt",
            "done\n",
            f"feat: lane {name} ({child})",
        )

    session_state = supervise.derive_session(repo, root)
    routed = supervise.route_outcomes(repo, session_state, (_green(first), _green(second)))
    assert [r.route for r in routed] == ["merged", "merged"], [r.detail for r in routed]

    for child in (first, second):
        blocking = {
            str(dep["id"])
            for dep in _show(repo, child).get("dependencies") or []
            if dep.get("dependency_type") == "blocks"
        }
        assert blocking == set(), f"{child} gained an invented coupling: {blocking}"


def test_a_pass_attributes_the_coupling_the_same_way_whichever_lane_bounced(
    harness_repo: Path,
) -> None:

    repo = harness_repo
    root = _create_bead(repo, "the attribution root", issue_type="epic")
    alpha = _create_bead(repo, "lane declaring the shared file", parent=root)
    beta = _create_bead(repo, "lane declaring the shared tree", parent=root)
    for child, scope in ((alpha, "src/shared.py"), (beta, "src/*.py")):
        _tracker(repo, "dep", "add", child, root, "-t", "parent-child")
        _tracker(
            repo,
            "update",
            child,
            "-d",
            "## Trigger\n\nWhen run, I want scope kept, so I can trust it.\n\n"
            f"## Acceptance Criteria\n\n- Given it when landed then it holds\n"
            f"\n## Scope\n\n- `{scope}`\n",
        )

    conflicts = ("src/shared.py",)
    forward = merge.record_pass_couplings(repo, [(alpha, conflicts)], [beta])
    backward = merge.record_pass_couplings(repo, [(beta, conflicts)], [alpha])

    assert forward == {alpha: (beta,)}, "the declared scope did not come back out of br"
    assert backward == {beta: (alpha,)}
    lower, higher = sorted((alpha, beta))
    for bead, expected in ((lower, {higher: merge.COUPLING_DEP_TYPE}), (higher, {})):
        coupled = {
            str(dep["id"]): dep.get("dependency_type")
            for dep in _show(repo, bead).get("dependencies") or []
            if str(dep["id"]) in (alpha, beta)
        }
        assert coupled == expected


@pytest.mark.parametrize("granted", [True, False], ids=["covered", "uncovered"])
def test_a_decomposed_root_seeds_a_lane_only_under_a_covering_grant(
    harness_repo: Path, granted: bool
) -> None:

    epic = _create_bead(harness_repo, "the decomposed epic", issue_type="epic")
    child = _create_bead(harness_repo, "the ready child", parent=epic)
    if granted:
        issued = policy.issue_grant_guarded(
            harness_repo,
            epic,
            "L1",
            100_000_000,
            load_policy_config(harness_repo),
            interactive=True,
        )
        assert issued.status == "approved", issued.detail
    assert loop_state.read_node_state(harness_repo, epic).phase == "decompose"
    assert not policy.checkpoint_approved(harness_repo, epic, "decompose")

    routed = supervise.seed_lanes(harness_repo, supervise.derive_session(harness_repo, epic))

    if not granted:
        assert [r.route for r in routed] == ["seed-blocked"], [r.detail for r in routed]
        assert "decompose" in routed[0].detail
        assert "L1" in routed[0].detail, "the refusal must name the level that would cover it"
        assert not worktree.list_sessions(harness_repo)
        return
    assert [r.route for r in routed] == ["seeded"], [r.detail for r in routed]
    assert supervise.should_continue(routed)
    assert loop_state.read_node_state(harness_repo, child).phase == "build"
    assert [session.name for session in worktree.list_sessions(harness_repo)] == [
        child.replace(".", "-")
    ]

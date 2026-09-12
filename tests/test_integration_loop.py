from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import (
    decisions,
    loop,
    loop_state,
    merge,
    needs_input,
    policy,
    run_record,
    runner,
    supervise,
    tracker,
    worktree,
)
from basicly.config import VERIFY_GATE_PROVIDER, load_policy_config
from basicly.decompose import ChildSpec
from tests import flipped_tracker, standin_agent

_GATED = {
    "depends_on": (),
    "budget_tokens": 40_000,
    "integrity": "L2",
    "demonstration": "run `basicly loop status` and read the lane phase",
}

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

_STANDIN_RUNNER_CONFIG = f"""
[runner]
default = "standin"

[[runner.agents]]
name = "standin"
command = [
  {Path(sys.executable).as_posix()!r},
  {(Path(__file__).parent / "standin_agent.py").as_posix()!r},
  "-p",
  "{{prompt}}",
]
usage_format = "claude-stream-json"
"""


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout


def _is_merged(repo: Path, branch: str) -> bool:
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, "main"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


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


def _status(repo: Path, issue_id: str) -> str:
    return str(_show(repo, issue_id)["status"])


def _subtasks_by_title(repo: Path, parent: str) -> dict[str, str]:

    return {
        str(dep["title"]): str(dep["id"])
        for dep in _show(repo, parent).get("dependents") or []
        if dep.get("dependency_type") == "parent-child"
    }


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


def test_a_leaf_lands_records_its_gate_and_closes(harness_repo: Path) -> None:
    repo = harness_repo
    issue = _create_bead(repo, "add the greeting")

    provisioned = _to_build(repo, issue)
    assert provisioned.blocked
    state = loop_state.read_node_state(repo, issue)
    assert state.phase == "build"
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    branch = session.branch
    tree = Path(session.worktree_path)
    assert tree.is_dir()

    _commit(tree, "app.txt", "start\ngreeting\n", f"feat: add the greeting ({issue})")

    landed = loop.advance(repo, issue)
    assert landed.action == "merged", landed.detail
    assert landed.to_phase == "verify"

    assert (repo / "app.txt").read_text(encoding="utf-8") == "start\ngreeting\n"
    assert _git(repo, "log", "-1", "--format=%s", "main").strip().startswith("chore(worktree):")
    assert _is_merged(repo, branch)

    gates = loop_state.read_node_state(repo, issue).gates
    assert gates.required_passed == ("verify",)
    assert gates.can_advance

    policy.approve_checkpoint(repo, issue, "ship")
    assert loop_state.read_node_state(repo, issue).phase == "ship"
    shipped = loop.advance(repo, issue)
    assert shipped.action == "tore-down", shipped.detail
    assert shipped.to_phase == "done"

    assert _status(repo, issue) == "closed"
    assert worktree.load_session(state.worktree.name, repo) is None
    assert not tree.exists()
    assert _git(repo, "status", "--porcelain").strip() == ""


def test_ship_refuses_a_leaf_whose_branch_never_merged(harness_repo: Path) -> None:
    repo = harness_repo
    issue = _create_bead(repo, "strand the work")

    _to_build(repo, issue)
    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    _commit(
        Path(session.worktree_path), "app.txt", "stranded\n", f"feat: strand the work ({issue})"
    )

    _tracker(
        repo,
        "gate",
        "report",
        issue,
        "--gate",
        "verify",
        "--provider",
        VERIFY_GATE_PROVIDER,
        "--status",
        "pass",
    )
    assert loop_state.read_node_state(repo, issue).phase == "verify"
    policy.approve_checkpoint(repo, issue, "ship")
    assert loop_state.read_node_state(repo, issue).phase == "ship"

    refused = loop.advance(repo, issue)
    assert refused.blocked
    assert "not merged" in refused.detail
    assert _status(repo, issue) != "closed"
    assert Path(session.worktree_path).is_dir()


def test_a_red_verify_check_keeps_the_branch_unmerged(harness_repo: Path) -> None:
    repo = harness_repo
    issue = _create_bead(repo, "land a red change")

    _to_build(repo, issue)
    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)
    _commit(tree, SENTINEL, "red\n", f"feat: land a red change ({issue})")

    blocked = loop.advance(repo, issue)
    assert blocked.blocked, blocked.detail
    assert blocked.landing is not None
    assert blocked.landing.status == "verify-failed"
    assert policy.rework_attempts(repo, issue, merge.MERGE_GATE) == 1
    assert not (repo / SENTINEL).exists()
    assert not _is_merged(repo, session.branch)


def test_landing_blocks_instead_of_reworking_an_uncommitted_worktree(harness_repo: Path) -> None:
    repo = harness_repo
    issue = _create_bead(repo, "forget to commit")

    _to_build(repo, issue)
    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    (Path(session.worktree_path) / "app.txt").write_text("uncommitted\n", encoding="utf-8")

    blocked = loop.advance(repo, issue)
    assert blocked.landing is not None
    assert blocked.landing.status == "not-ready"
    assert policy.rework_attempts(repo, issue, merge.MERGE_GATE) == 0


def test_a_lane_runs_its_sub_tasks_in_sequence_then_integrates(harness_repo: Path) -> None:
    repo = harness_repo
    lane = _create_bead(repo, "build the parser package")

    _to_build(repo, lane)
    state = loop_state.read_node_state(repo, lane)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)

    plan = (
        ChildSpec(
            "tokenize the input",
            ("Given input when tokenized then tokens",),
            ("app.txt",),
            **_GATED,
        ),
        ChildSpec(
            "parse the tokens", ("Given tokens when parsed then a tree",), ("app.txt",), **_GATED
        ),
    )
    recorded = loop.advance(repo, lane, inputs=loop.Inputs(children=plan))
    assert recorded.blocked
    assert "2 lane sub-task(s)" in recorded.detail

    by_title = _subtasks_by_title(repo, lane)
    assert len(by_title) == 2
    subtasks = [by_title[spec.title] for spec in plan]

    for index, subtask in enumerate(subtasks, start=1):
        dispatched = loop.advance(repo, lane)
        assert dispatched.blocked, dispatched.detail
        assert subtask in dispatched.detail
        assert dispatched.from_phase == "build"

        _commit(tree, f"part{index}.txt", "done\n", f"feat: sub-task {index} ({subtask})")

        stepped = loop.advance(repo, lane)
        assert stepped.action == "sub-task", stepped.detail
        assert stepped.progressed
        assert not stepped.advanced
        assert _status(repo, subtask) == "closed"

    integrated = loop.advance(repo, lane)
    assert integrated.action == "merged", integrated.detail
    assert integrated.to_phase == "verify"
    assert (repo / "part1.txt").exists()
    assert (repo / "part2.txt").exists()

    gates = loop_state.read_node_state(repo, lane).gates
    assert gates.required_passed == ("verify",)
    assert gates.can_advance


def test_a_sub_task_is_not_closed_by_a_sibling_whose_id_extends_it(harness_repo: Path) -> None:

    repo = harness_repo
    lane = _create_bead(repo, "collide the sub-task ids")

    _to_build(repo, lane)
    state = loop_state.read_node_state(repo, lane)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)

    plan = tuple(
        ChildSpec(f"step {n}", (f"Given step {n} when run then done",), ("app.txt",), **_GATED)
        for n in range(1, 11)
    )
    loop.advance(repo, lane, inputs=loop.Inputs(children=plan))
    by_title = _subtasks_by_title(repo, lane)
    assert len(by_title) == 10
    first, tenth = by_title["step 1"], by_title["step 10"]
    assert tenth.startswith(first), f"the fixture needs nested ids, got {first} and {tenth}"

    loop.advance(repo, lane)
    _commit(tree, "ten.txt", "done\n", f"feat: the tenth step ({tenth})")

    still_waiting = loop.advance(repo, lane)
    assert still_waiting.blocked, still_waiting.detail
    assert first in still_waiting.detail
    assert _status(repo, first) != "closed"


def test_the_engine_commits_the_claim_before_provisioning(harness_repo: Path) -> None:
    repo = harness_repo
    issue = _create_bead(repo, "publish the claim")
    assert _git(repo, "status", "--porcelain").strip() != ""

    _to_build(repo, issue)
    assert _git(repo, "log", "-1", "--format=%s").strip() == (
        f"chore(beads): record the claim before provisioning ({issue})"
    )
    tracked = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert tracked and all(path.startswith(".basicly/ledger/") for path in tracked)


def test_a_non_tracker_dirty_base_is_left_alone(harness_repo: Path) -> None:
    repo = harness_repo
    issue = _create_bead(repo, "guard the base")
    (repo / "app.txt").write_text("someone else was here\n", encoding="utf-8")

    assert merge.commit_tracker_state(repo, issue) is False
    assert "app.txt" in _git(repo, "status", "--porcelain")


@pytest.fixture
def standin_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:

    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    runner.reset_process_budget()
    return _seed_repo(tmp_path, _STANDIN_RUNNER_CONFIG)


def _set_modes(monkeypatch: pytest.MonkeyPatch, **modes: str) -> None:
    monkeypatch.setenv(standin_agent.MODES_ENV, json.dumps(modes))


def _dispatches(repo: Path, issue_id: str) -> list[dict]:
    return run_record.dispatch_history(repo).get(issue_id, [])


def _merge_commits(repo: Path) -> list[str]:

    subjects = _git(repo, "log", "--format=%s", "--first-parent", "main").splitlines()
    return [subject for subject in subjects if subject.startswith("chore(worktree):")]


def test_a_dispatched_agent_cli_commits_and_the_loop_lands_it(standin_repo: Path) -> None:

    repo = standin_repo
    issue = _create_bead(repo, "let the agent do the work")

    dispatched = _to_build(repo, issue)
    assert dispatched.blocked
    assert "advance again to land it" in dispatched.detail, dispatched.detail

    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)
    assert (tree / f"{issue}.txt").read_text(encoding="utf-8") == f"work for {issue}\n"
    assert _git(tree, "log", "-1", "--format=%s").strip() == f"feat: stand-in work ({issue})"
    assert not _is_merged(repo, session.branch)

    landed = loop.advance(repo, issue)
    assert landed.action == "merged", landed.detail
    assert landed.to_phase == "verify"
    assert (repo / f"{issue}.txt").read_text(encoding="utf-8") == f"work for {issue}\n"
    assert _is_merged(repo, session.branch)
    assert loop_state.read_node_state(repo, issue).gates.required_passed == ("verify",)

    records = _dispatches(repo, issue)
    assert len(records) == 1, records
    assert records[0]["agent"] == "standin"
    assert records[0]["outcome"] == run_record.EXECUTED
    assert records[0]["returncode"] == 0
    assert records[0]["estimated"] is False
    assert records[0]["tokens"] == standin_agent.DEFAULT_OCCUPANCY

    policy.approve_checkpoint(repo, issue, "ship")
    shipped = loop.advance(repo, issue)
    assert shipped.action == "tore-down", shipped.detail
    assert _status(repo, issue) == "closed"


def test_a_dispatched_agent_that_exits_non_zero_blocks_and_lands_nothing(
    standin_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = standin_repo
    _set_modes(monkeypatch, default=standin_agent.FAIL)
    issue = _create_bead(repo, "watch the agent fall over")

    blocked = _to_build(repo, issue)
    assert blocked.blocked
    assert f"exit {standin_agent.FAIL_CODE}" in blocked.detail, blocked.detail
    assert standin_agent.FAIL_MESSAGE in blocked.detail, blocked.detail

    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)
    assert _git(tree, "log", "--format=%s", f"main..{session.branch}").strip() == ""
    assert _merge_commits(repo) == []
    assert _status(repo, issue) != "closed"
    assert policy.rework_attempts(repo, issue, merge.MERGE_GATE) == 0

    records = _dispatches(repo, issue)
    assert [record["outcome"] for record in records] == [run_record.FAILED]
    assert records[0]["returncode"] == standin_agent.FAIL_CODE


def test_a_dispatched_agent_that_cannot_resolve_a_fact_surfaces_it(
    standin_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = standin_repo
    _set_modes(monkeypatch, default=standin_agent.NEEDS_INPUT)
    issue = _create_bead(repo, "make the agent block")

    blocked = _to_build(repo, issue)
    assert blocked.needs_input == standin_agent.NEEDS_FACT, blocked.detail
    assert standin_agent.NEEDS_DETAIL in blocked.detail

    queued = [item for item in decisions.pending(repo, issue) if item.kind == "needs-input"]
    assert [item.question for item in queued] == [standin_agent.NEEDS_FACT]
    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)
    assert not (tree / needs_input.SENTINEL_FILE).exists()
    assert _git(tree, "log", "--format=%s", f"main..{session.branch}").strip() == ""
    assert _merge_commits(repo) == []


def test_a_supervisor_pass_runs_two_agent_processes_and_lands_both(
    standin_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = standin_repo
    _set_modes(monkeypatch, default=standin_agent.IDLE)
    root = _create_bead(repo, "the concurrent root", issue_type="epic")
    first = _create_bead(repo, "lane one", parent=root)
    second = _create_bead(repo, "lane two", parent=root)
    for child in (first, second):
        provisioned = _to_build(repo, child)
        assert provisioned.blocked, provisioned.detail

    granted = policy.issue_grant_guarded(
        repo, root, "L2", 100_000_000, load_policy_config(repo), interactive=True
    )
    assert granted.status == "approved", granted.detail

    _set_modes(monkeypatch, default=standin_agent.COMMIT)
    session_state = supervise.derive_session(repo, root)
    outcomes = supervise.dispatch_lanes(repo, session_state)

    assert {outcome.issue_id for outcome in outcomes} == {first, second}
    for outcome in outcomes:
        assert outcome.result is not None, outcome.detail
        assert outcome.result.executed, outcome.detail
        assert outcome.result.returncode == 0, outcome.detail
        assert outcome.detail == "finished; ready to land"
        assert outcome.result.command[0] == Path(sys.executable).as_posix()

    routed = supervise.route_outcomes(repo, session_state, outcomes)
    assert {r.issue_id for r in routed} == {first, second}
    assert [r.route for r in routed] == ["merged", "merged"], [r.detail for r in routed]

    for child in (first, second):
        assert (repo / f"{child}.txt").read_text(encoding="utf-8") == f"work for {child}\n"
        assert loop_state.read_node_state(repo, child).gates.required_passed == ("verify",)
        assert [record["outcome"] for record in _dispatches(repo, child)] == [
            run_record.EXECUTED,
            run_record.EXECUTED,
        ]
    assert len(_merge_commits(repo)) == 2

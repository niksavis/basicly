"""End-to-end loop tests against a real git repository and a real ``br`` tracker.

Every other loop/supervise test stubs the two things the loop is actually made
of. That is the right shape for branch coverage, and it is also why three
shipped defects reached main:

- basicly-kjc5.9: the sub-task commit probe substring-matched, so ``x.10``'s
  commit closed ``x.1``.
- basicly-kjc5.10: ``br show --json`` spells a dependency ``id`` /
  ``dependency_type`` while the ``dep add`` echo spells it ``depends_on_id`` /
  ``type``; parsing one shape made the landing order silently empty.
- basicly-kjc5.10: ``lstrip("./")`` ate the leading dot of ``.beads/``, so the
  engine-path filter matched nothing and every landing recorded a false
  coupling.

A stub cannot disagree with the tool it stands for, so none of those were
reachable from the unit suite; each was found in a hand-built scratchpad
sandbox and the recipe stayed tribal knowledge. This module *is* that recipe:
a fixture repository with real git history and a real tracker, driven through
:func:`basicly.loop.advance` with nothing between the engine and ``git``/``br``.

What is still substituted, and why it is not the thing under test:

- ``worktree.install_worktree_hooks`` — ``pre-commit install`` against the
  bundled hook manifest, i.e. a third-party tool provisioning a repo the test
  did not write. ``tests/test_worktree.py`` substitutes it for the same reason.
- the coding agent — ``[runner] default = "manual"`` is a real configuration,
  not a stub: the handoff runner blocks for its driver, and the test then plays
  the agent by committing on the harness branch, which is exactly the contract a
  human or a headless adapter fulfils.

``provision_deps`` is left alone: the fixture carries no ``pyproject.toml`` or
``package.json``, so the real function runs and correctly installs nothing.
"""

from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import br, loop, loop_state, merge, policy, runner, supervise, worktree
from basicly.decompose import ChildSpec

needs_br = pytest.mark.skipif(
    br.which() is None, reason="the beads tracker (br) is not installed on this machine"
)

# A verify check the test can make fail on demand, so a red landing is a real
# subprocess verdict rather than a patched return value. Uses the running
# interpreter (as_posix so a Windows path survives TOML) and nothing else.
SENTINEL = "BROKEN"
_PROBE = f"import pathlib,sys; sys.exit(1 if pathlib.Path({SENTINEL!r}).exists() else 0)"

CONFIG = f"""\
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

[policy.sizing]
# The fixture's files are tiny; without this floor the sizing governor refuses
# every plan as under-scoped before a child is ever created.
working_set_min = 1

[runner]
default = "manual"
"""


def _git(cwd: Path, *args: str) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)
    return proc.stdout


def _is_merged(repo: Path, branch: str) -> bool:
    """True when *branch*'s tip is an ancestor of ``main`` — a real merge, not a claim."""
    probe = subprocess.run(
        ["git", "merge-base", "--is-ancestor", branch, "main"],
        cwd=repo,
        check=False,
        capture_output=True,
    )
    return probe.returncode == 0


def _br(cwd: Path, *args: str) -> str:
    """Run the real ``br``, failing loudly — the fixture has no tracker to fake."""
    return br.run_br(cwd, list(args)).stdout


def _commit(cwd: Path, path: str, body: str, message: str) -> None:
    (cwd / path).write_text(body, encoding="utf-8")
    _git(cwd, "add", "--", path)
    _git(cwd, "commit", "-m", message)


def _create_bead(repo: Path, title: str, *, issue_type: str = "task") -> str:
    """Create one bead carrying acceptance criteria, so the DoR gate passes."""
    out = _br(
        repo,
        "create",
        title,
        "-t",
        issue_type,
        "-d",
        f"## Acceptance Criteria\n\n- Given the fixture when {title} then it lands\n",
        "--json",
    )
    return str(json.loads(out)["id"])


def _show(repo: Path, issue_id: str) -> dict:
    data = json.loads(_br(repo, "show", issue_id, "--json"))
    return data[0] if isinstance(data, list) else data


def _status(repo: Path, issue_id: str) -> str:
    return str(_show(repo, issue_id)["status"])


def _subtasks_by_title(repo: Path, parent: str) -> dict[str, str]:
    """``{title: id}`` for the parent's sub-tasks, from ``br``'s own dependent shape.

    Keyed on title rather than position on purpose: ``br`` returns dependents in
    neither creation nor id order, so a positional read would pin the tracker's
    incidental ordering instead of the lane's.
    """
    return {
        str(dep["title"]): str(dep["id"])
        for dep in _show(repo, parent).get("dependents") or []
        if dep.get("dependency_type") == "parent-child"
    }


@pytest.fixture
def harness_repo(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    """A consumer repo with real git history, a real br workspace, and a config."""
    repo = tmp_path / "consumer"
    repo.mkdir()
    _git(repo, "init", "-b", "main")
    _git(repo, "config", "user.name", "Harness Test")
    _git(repo, "config", "user.email", "harness@example.invalid")
    _git(repo, "config", "commit.gpgsign", "false")
    (repo / "basicly.toml").write_text(CONFIG, encoding="utf-8")
    (repo / "app.txt").write_text("start\n", encoding="utf-8")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: seed the fixture repo")

    _br(repo, "init", "--prefix", "fx")
    _br(repo, "sync", "--flush-only")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-m", "chore: initialize the beads workspace")

    # pre-commit installing the bundled hook manifest into a repo that has no
    # .pre-commit-config.yaml, over the network. Not the engine under test.
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    return repo


def _to_build(repo: Path, issue_id: str) -> loop.AdvanceResult:
    """Drive intake -> classify -> a provisioned worktree, approving the checkpoint."""
    intake = loop.advance(repo, issue_id, inputs=loop.Inputs(work_type="task"))
    assert intake.checkpoint == "classify", intake.detail
    policy.approve_checkpoint(repo, issue_id, "classify")
    return loop.advance(repo, issue_id)


# --- A leaf: provision, commit, land, ship ----------------------------------


@needs_br
def test_a_leaf_lands_records_its_gate_and_closes(harness_repo: Path) -> None:
    """The whole leaf boundary against real git and real br: merge, gate, close."""
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

    # Play the agent the manual runner handed off to.
    _commit(tree, "app.txt", "start\ngreeting\n", f"feat: add the greeting ({issue})")

    landed = loop.advance(repo, issue)
    assert landed.action == "merged", landed.detail
    assert landed.to_phase == "verify"

    # The merge really happened in the base checkout.
    assert (repo / "app.txt").read_text(encoding="utf-8") == "start\ngreeting\n"
    assert _git(repo, "log", "-1", "--format=%s", "main").strip().startswith("chore(worktree):")
    assert _is_merged(repo, branch)

    # The required gate was recorded on the bead by the landing, not by hand.
    gates = loop_state.read_node_state(repo, issue).gates
    assert gates.required_passed == ("verify",)
    assert gates.can_advance

    # Approving the ship checkpoint *is* what derives the phase to ship, so the
    # next advance is already the teardown — there is no separate ship step.
    policy.approve_checkpoint(repo, issue, "ship")
    assert loop_state.read_node_state(repo, issue).phase == "ship"
    shipped = loop.advance(repo, issue)
    assert shipped.action == "tore-down", shipped.detail
    assert shipped.to_phase == "done"

    assert _status(repo, issue) == "closed"
    assert worktree.load_session(state.worktree.name, repo) is None
    assert not tree.exists()
    assert _git(repo, "status", "--porcelain").strip() == ""


@needs_br
def test_ship_refuses_a_leaf_whose_branch_never_merged(harness_repo: Path) -> None:
    """Recording the verify gate out-of-band must not ship code stranded on a branch."""
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

    # The out-of-band gate record that derives the phase straight past the merge.
    _br(
        repo,
        "gate",
        "report",
        issue,
        "--gate",
        "verify",
        "--provider",
        "by-hand",
        "--status",
        "pass",
    )
    assert loop_state.read_node_state(repo, issue).phase == "verify"
    policy.approve_checkpoint(repo, issue, "ship")
    assert loop_state.read_node_state(repo, issue).phase == "ship"

    refused = loop.advance(repo, issue)
    assert refused.blocked
    assert "not merged" in refused.detail
    # No side effects: the bead is open and the worktree still stands.
    assert _status(repo, issue) != "closed"
    assert Path(session.worktree_path).is_dir()


@needs_br
def test_a_red_verify_check_keeps_the_branch_unmerged(harness_repo: Path) -> None:
    """A genuinely failing subprocess check blocks the landing and spends rework."""
    repo = harness_repo
    issue = _create_bead(repo, "land a red change")

    _to_build(repo, issue)
    state = loop_state.read_node_state(repo, issue)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)
    # The sentinel check exits 1 while this file exists, so verify fails for real.
    _commit(tree, SENTINEL, "red\n", f"feat: land a red change ({issue})")

    blocked = loop.advance(repo, issue)
    assert blocked.blocked, blocked.detail
    assert blocked.landing is not None
    assert blocked.landing.status == "verify-failed"
    # A failed landing spends an attempt on the merge gate, not on verify.
    assert policy.rework_attempts(repo, issue, merge.MERGE_GATE) == 1
    assert not (repo / SENTINEL).exists()
    assert not _is_merged(repo, session.branch)


@needs_br
def test_landing_blocks_instead_of_reworking_an_uncommitted_worktree(harness_repo: Path) -> None:
    """An operator-fixable state must not burn a bounded rework attempt (basicly-4psl)."""
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


# --- A lane: sub-tasks in sequence, then integration ------------------------


@needs_br
def test_a_lane_runs_its_sub_tasks_in_sequence_then_integrates(harness_repo: Path) -> None:
    """The lane mini-loop end to end: two real sub-task beads, closed by real commits."""
    repo = harness_repo
    lane = _create_bead(repo, "build the parser package")

    _to_build(repo, lane)
    state = loop_state.read_node_state(repo, lane)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)

    plan = (
        ChildSpec("tokenize the input", ("Given input when tokenized then tokens",), ("app.txt",)),
        ChildSpec("parse the tokens", ("Given tokens when parsed then a tree",), ("app.txt",)),
    )
    recorded = loop.advance(repo, lane, inputs=loop.Inputs(children=plan))
    assert recorded.blocked
    assert "2 lane sub-task(s)" in recorded.detail

    # Both sub-tasks declare the same scope, so decompose serialized them in
    # declared order — that chain is what fixes the lane's run order.
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


@needs_br
def test_a_sub_task_is_not_closed_by_a_sibling_whose_id_extends_it(harness_repo: Path) -> None:
    """``x.1`` must not be closed by a commit naming ``x.10`` (basicly-kjc5.9).

    The prefix collision is only reachable against real ``br`` id minting and a
    real git log; a stubbed tracker hands back whatever ids the test invented.
    """
    repo = harness_repo
    lane = _create_bead(repo, "collide the sub-task ids")

    _to_build(repo, lane)
    state = loop_state.read_node_state(repo, lane)
    assert state.worktree is not None
    session = worktree.load_session(state.worktree.name, repo)
    assert session is not None
    tree = Path(session.worktree_path)

    plan = tuple(
        ChildSpec(f"step {n}", (f"Given step {n} when run then done",), ("app.txt",))
        for n in range(1, 11)
    )
    loop.advance(repo, lane, inputs=loop.Inputs(children=plan))
    by_title = _subtasks_by_title(repo, lane)
    assert len(by_title) == 10
    first, tenth = by_title["step 1"], by_title["step 10"]
    assert tenth.startswith(first), f"the fixture needs nested ids, got {first} and {tenth}"

    # Commit only the *tenth* sub-task's work; the first must still be undone.
    loop.advance(repo, lane)  # dispatch sub-task 1 (handoff)
    _commit(tree, "ten.txt", "done\n", f"feat: the tenth step ({tenth})")

    still_waiting = loop.advance(repo, lane)
    assert still_waiting.blocked, still_waiting.detail
    assert first in still_waiting.detail
    assert _status(repo, first) != "closed"


# --- Tracker commits, made by the engine and by nobody else -----------------


@needs_br
def test_the_engine_commits_the_claim_before_provisioning(harness_repo: Path) -> None:
    """Tracker dirt is rolled into a chore commit at each of the three points."""
    repo = harness_repo
    issue = _create_bead(repo, "publish the claim")
    assert _git(repo, "status", "--porcelain").strip() != ""

    _to_build(repo, issue)
    assert _git(repo, "log", "-1", "--format=%s").strip() == (
        f"chore(beads): record the claim before provisioning ({issue})"
    )
    tracked = _git(repo, "show", "--name-only", "--format=", "HEAD").split()
    assert tracked and all(path.startswith(".beads/") for path in tracked)


@needs_br
def test_a_non_tracker_dirty_base_is_left_alone(harness_repo: Path) -> None:
    """``commit_tracker_state`` refuses to sweep up somebody else's uncommitted work."""
    repo = harness_repo
    issue = _create_bead(repo, "guard the base")
    (repo / "app.txt").write_text("someone else was here\n", encoding="utf-8")

    assert merge.commit_tracker_state(repo, issue) is False
    assert "app.txt" in _git(repo, "status", "--porcelain")


# --- A supervisor pass over two real lanes ----------------------------------


def _green(issue_id: str) -> supervise.LaneOutcome:
    """The outcome a headless adapter produces when its dispatch succeeds.

    The one thing this module synthesizes rather than performs: the fixture's
    runner is the ``manual`` handoff, which by contract hands off instead of
    writing code, so no real dispatch is ever green. Everything downstream of
    it — the landing order, the merges, the gates, the tracker writes — is real.
    """
    return supervise.LaneOutcome(
        issue_id=issue_id,
        runner_name="fixture",
        result=runner.RunResult(runner="fixture", command=(), executed=True, returncode=0),
        needs_fact=None,
        occupancy=None,
        overrun=False,
        followup_id=None,
        detail="fixture dispatch",
    )


@needs_br
def test_a_supervisor_pass_lands_two_lanes_in_dependency_order(harness_repo: Path) -> None:
    """Two green lanes land in ``br``'s dependency order, not in arrival order.

    Pins the basicly-kjc5.10 shape: ``merge.landing_order`` reads dependencies
    out of ``br show --json``, which spells them ``id``/``dependency_type``
    while the ``dep add`` echo spells them ``depends_on_id``/``type``. Reading
    the wrong shape leaves the order silently empty, which no stubbed tracker
    can disagree with.
    """
    repo = harness_repo
    root = _create_bead(repo, "the session root", issue_type="epic")
    first = _create_bead(repo, "the earlier lane")
    second = _create_bead(repo, "the later lane")
    for child in (first, second):
        _br(repo, "dep", "add", child, root, "-t", "parent-child")
    # The later lane genuinely depends on the earlier one.
    _br(repo, "dep", "add", second, first, "-t", "blocks")

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

    # Hand them over in the *wrong* order; the dependency edge must reorder them.
    routed = supervise.route_outcomes(repo, session_state, (_green(second), _green(first)))
    assert [r.issue_id for r in routed] == [first, second]
    assert [r.route for r in routed] == ["merged", "merged"], [r.detail for r in routed]

    # Both landings are real merges in the base checkout, in that order.
    assert (repo / "first.txt").exists()
    assert (repo / "second.txt").exists()
    subjects = _git(repo, "log", "--format=%s", "--first-parent", "main").splitlines()
    merges = [s for s in subjects if s.startswith("chore(worktree):")]
    assert len(merges) == 2

    for child in (first, second):
        assert loop_state.read_node_state(repo, child).gates.required_passed == ("verify",)


@needs_br
def test_a_landing_pass_invents_no_coupling_from_the_shared_tracker(harness_repo: Path) -> None:
    """Every landing rewrites ``.beads/**``; that must not read as a scope collision.

    Pins the second basicly-kjc5.10 shape: ``lstrip("./")`` ate the leading dot
    of ``.beads/``, so the engine-path filter matched nothing and each landing
    attributed a false ``blocks`` edge to the lane that landed before it.
    """
    repo = harness_repo
    root = _create_bead(repo, "the coupling root", issue_type="epic")
    first = _create_bead(repo, "lane alpha")
    second = _create_bead(repo, "lane beta")
    for child in (first, second):
        _br(repo, "dep", "add", child, root, "-t", "parent-child")

    for name, child in (("alpha", first), ("beta", second)):
        _to_build(repo, child)
        state = loop_state.read_node_state(repo, child)
        assert state.worktree is not None
        session = worktree.load_session(state.worktree.name, repo)
        assert session is not None
        # Disjoint files: the only path both landings touch is the tracker's.
        _commit(
            Path(session.worktree_path),
            f"{name}.txt",
            "done\n",
            f"feat: lane {name} ({child})",
        )

    session_state = supervise.derive_session(repo, root)
    routed = supervise.route_outcomes(repo, session_state, (_green(first), _green(second)))
    assert [r.route for r in routed] == ["merged", "merged"], [r.detail for r in routed]

    # No lane acquired a dependency it did not declare.
    for child in (first, second):
        blocking = {
            str(dep["id"])
            for dep in _show(repo, child).get("dependencies") or []
            if dep.get("dependency_type") == "blocks"
        }
        assert blocking == set(), f"{child} gained an invented coupling: {blocking}"


@needs_br
def test_a_pass_attributes_the_coupling_the_same_way_whichever_lane_bounced(
    harness_repo: Path,
) -> None:
    """The coupling edge is a function of the declared scopes, not of landing order.

    Pins basicly-kjc5.32 on the two things no stubbed tracker can decide: the
    ``## Scope`` section really has to come back out of a real bead, and ``br``
    really has to hold the resulting edge in one direction. So the same pass is
    attributed twice with the two lanes' roles swapped — as reversing their
    completion order does — and both must write the identical edge.
    """
    repo = harness_repo
    root = _create_bead(repo, "the attribution root", issue_type="epic")
    alpha = _create_bead(repo, "lane declaring the shared file")
    beta = _create_bead(repo, "lane declaring the shared tree")
    for child, scope in ((alpha, "src/shared.py"), (beta, "src/*.py")):
        _br(repo, "dep", "add", child, root, "-t", "parent-child")
        _br(
            repo,
            "update",
            child,
            "-d",
            f"## Acceptance Criteria\n\n- Given it when landed then it holds\n"
            f"\n## Scope\n\n- `{scope}`\n",
        )

    conflicts = ("src/shared.py",)
    # alpha bounced and beta landed, then the reverse — the same collision seen
    # from each side of the pass.
    forward = merge.record_pass_couplings(repo, [(alpha, conflicts)], [beta])
    backward = merge.record_pass_couplings(repo, [(beta, conflicts)], [alpha])

    assert forward == {alpha: (beta,)}, "the declared scope did not come back out of br"
    assert backward == {beta: (alpha,)}
    # One edge in the tracker, in the canonical direction, not two opposed ones.
    lower, higher = sorted((alpha, beta))
    for bead, expected in ((lower, {higher: merge.COUPLING_DEP_TYPE}), (higher, {})):
        coupled = {
            str(dep["id"]): dep.get("dependency_type")
            for dep in _show(repo, bead).get("dependencies") or []
            if str(dep["id"]) in (alpha, beta)
        }
        assert coupled == expected


@needs_br
def test_a_landing_cancels_the_lane_it_broke_and_tells_it_why(harness_repo: Path) -> None:
    """A lane a landing broke is cancelled, informed, and left free to re-dispatch (D6).

    Pins basicly-kjc5.26 end to end, on the two things no stubbed tracker can
    disagree with: ``git merge-tree`` really has to report the collision the
    first landing created, and the record the supervisor publishes really has to
    come back out of ``build_bundle`` in the cancelled lane's next prompt. A
    gating ``blocks`` edge is the failure mode being excluded — the lane that
    landed is merged but *not shipped*, so an edge onto it would drop the
    cancelled lane out of the ready set and hold it behind a human.
    """
    repo = harness_repo
    root = _create_bead(repo, "the collision root", issue_type="epic")
    first = _create_bead(repo, "lane that lands first")
    second = _create_bead(repo, "lane that gets cancelled")
    for child in (first, second):
        _br(repo, "dep", "add", child, root, "-t", "parent-child")

    for name, child in (("first", first), ("second", second)):
        _to_build(repo, child)
        state = loop_state.read_node_state(repo, child)
        assert state.worktree is not None
        session = worktree.load_session(state.worktree.name, repo)
        assert session is not None
        # The same file, incompatible content: once one lands, the other's branch
        # no longer merges onto the base.
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
    # The cancelled lane's landing was never attempted: the base carries the
    # first lane's content and only its merge.
    assert (repo / "shared.txt").read_text(encoding="utf-8") == "the first lane wrote this\n"
    merges = [
        line
        for line in _git(repo, "log", "--format=%s", "--first-parent", "main").splitlines()
        if line.startswith("chore(worktree):")
    ]
    assert len(merges) == 1

    # Nothing gates the cancelled lane: it must be free to re-dispatch.
    blocking = {
        str(dep["id"])
        for dep in _show(repo, second).get("dependencies") or []
        if dep.get("dependency_type") == "blocks"
    }
    assert blocking == set(), f"{second} was gated instead of re-dispatched: {blocking}"

    # And its next dispatch prompt carries why, naming the lane that landed.
    bundle = supervise.build_bundle(repo, second, known_ids=frozenset({root, first, second}))
    assert [info.kind for info in bundle.folded] == ["coupling"]
    assert first in bundle.prompt


@needs_br
def test_a_missed_coupling_teaches_the_graph_without_gating_the_bounced_lane(
    harness_repo: Path,
) -> None:
    """A recorded coupling must not hold the lane the bounce exists to send back.

    Pins basicly-grrb, on the one thing no stubbed tracker can decide: whether
    ``br`` counts this edge in ``br blocked``. The bounce records the coupling
    onto the lane it collided with, and under the supervisor that lane is
    ``merged`` but parked in verify awaiting a ship checkpoint — still open. As a
    ``blocks`` edge that dropped the bounced lane out of ``ready_lanes``, holding
    it behind a human approval instead of re-dispatching it.
    """
    repo = harness_repo
    root = _create_bead(repo, "the coupling-edge root", issue_type="epic")
    landed = _create_bead(repo, "the lane that landed")
    bounced = _create_bead(repo, "the lane that bounced")
    for child in (landed, bounced):
        _br(repo, "dep", "add", child, root, "-t", "parent-child")
        _to_build(repo, child)

    # Exactly what a bounce writes, with the collided-with lane still open —
    # which is the state a supervisor landing leaves it in.
    merge.record_coupling(repo, bounced, landed)
    assert _show(repo, landed)["status"] != "closed"

    # The graph learned the coupling, written in the canonical direction — the two
    # ids sorted — so the edge is identical whichever lane bounced (kjc5.32).
    lower, higher = sorted((bounced, landed))
    coupled = {
        str(dep["id"]): dep.get("dependency_type")
        for dep in _show(repo, lower).get("dependencies") or []
    }
    assert coupled.get(higher) == merge.COUPLING_DEP_TYPE

    # ...and the bounced lane is still dispatchable on the next pass.
    assert bounced not in loop_state.blocked_ids(repo)
    session_state = supervise.derive_session(repo, root)
    ready = {lane.issue_id for lane in supervise.ready_lanes(repo, session_state)}
    assert bounced in ready, f"{bounced} was gated by the coupling it taught the graph"


@needs_br
def test_a_discovered_coupling_gates_and_orders_the_bead_it_names(harness_repo: Path) -> None:
    """A lane's coupling discovery teaches the real graph, which then holds the order.

    Pins basicly-kjc5.24 on what only ``br`` can answer: whether the proposed edge
    actually gates (``br blocked`` → ``ready_lanes``) and whether the landing order
    the merge queue computes from the tracker honours it. A lane already in flight
    is deliberately excluded from gating (basicly-grrb), so the gated bead here is
    one that has not started.
    """
    repo = harness_repo
    root = _create_bead(repo, "the discovery root", issue_type="epic")
    finder = _create_bead(repo, "the lane that discovers")
    named = _create_bead(repo, "the bead it names")
    for child in (finder, named):
        _br(repo, "dep", "add", child, root, "-t", "parent-child")
    _to_build(repo, finder)  # in flight; `named` has not started

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

    # br really gates it, so the pass will not dispatch the two in parallel...
    assert named in loop_state.blocked_ids(repo)
    # ...and the merge queue's dependency sort really honours the new edge.
    ordered = merge.landing_order(repo, [(finder, finder), (named, named)])
    assert [bead for _name, bead in ordered] == [finder, named]

    # Re-reading the same record on a later pass proposes nothing new.
    assert supervise.propose_coupling_edges(repo, supervise.derive_session(repo, root)) == ()

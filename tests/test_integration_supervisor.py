"""A supervisor pass over two real lanes: what lands, in what order, and why.

Split out of ``test_integration_loop.py``. A pass fans out over lanes that share
one tracker and one base branch, so the properties here are the ones only a real
pass can show: two lanes land in dependency order rather than in the order their
outcomes arrived; a landing invents no coupling out of the tracker files every
lane touches; and when one lane's landing bounces the other, the coupling is
attributed to the same pair whichever of the two got there first.

The couplings a pass *discovers* — cancellation, and teaching the graph — are next
door in ``test_integration_supervisor_coupling.py``.

The recipe is ``test_integration_loop.py``'s: a fixture repository with real
git history and a real ``br`` workspace, driven through the engine with nothing
between it and ``git``/``br``. The coding agent is the one substitution, and it is
a real configuration rather than a stub — ``[runner] default = "manual"`` blocks
for its driver and the test then plays the agent by committing on the harness
branch. ``worktree.install_worktree_hooks`` is stubbed for the same reason it is
there: provisioning a repo with ``pre-commit`` is a third-party tool, not the
engine under test.
"""

from __future__ import annotations

import subprocess
import sys
from pathlib import Path

import pytest

from basicly import loop, loop_state, merge, policy, runner, supervise, tracker, worktree
from tests import flipped_tracker

# A verify check the test can make fail on demand, so a red landing is a real
# subprocess verdict rather than a patched return value. Uses the running
# interpreter (as_posix so a Windows path survives TOML) and nothing else.
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


# The fixture's root record. Every bead a test creates is a child of it, because a mint
# with no parent needs a declared `[tracker] prefix` and the fixture's config is what the
# test is *not* about (`owned_write.create`).
_ROOT = "fx-1"


def _seed_tracker(repo: Path) -> None:
    """Give *repo* a ledger holding the root every created bead hangs off.

    The kit is copied in rather than installed, because these tests run the loop and not
    the installer; the root is opened through the kit for the same reason.
    """
    flipped_tracker.flipped_repo(repo)
    flipped_tracker.seed(repo, _ROOT, title="the fixture root", issue_type="epic")


def _tracker(cwd: Path, *args: str) -> None:
    """One tracker write through the engine seam, failing loudly.

    The fixture has no tracker to fake: these tests exercise the loop against a real
    ledger, so a write that did not land has to stop the test rather than be absorbed.
    """
    tracker.write(cwd, list(args))


def _commit(cwd: Path, path: str, body: str, message: str) -> None:
    (cwd / path).write_text(body, encoding="utf-8")
    _git(cwd, "add", "--", path)
    _git(cwd, "commit", "-m", message)


def _create_bead(repo: Path, title: str, *, issue_type: str = "task", parent: str = _ROOT) -> str:
    """Create one bead carrying acceptance criteria, so the DoR gate passes.

    *parent* is an argument rather than always the fixture root because the store mints a
    child id *under its parent*: a test that then adds its own ``parent-child`` edge would
    give the record two parents, and the fan-out reads the wrong one.
    """
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


def _show(repo: Path, issue_id: str) -> dict:
    return tracker.read_record(repo, issue_id) or {}


def _seed_repo(tmp_path: Path, runner_config: str) -> Path:
    """A consumer repo with real git history, a real br workspace, and a config."""
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
    """The fixture repo with the manual handoff runner: the test plays the agent."""
    # pre-commit installing the bundled hook manifest into a repo that has no
    # .pre-commit-config.yaml, over the network. Not the engine under test.
    monkeypatch.setattr(worktree, "install_worktree_hooks", lambda _wt: "hooks: stubbed")
    return _seed_repo(tmp_path, _MANUAL_RUNNER_CONFIG)


def _to_build(repo: Path, issue_id: str) -> loop.AdvanceResult:
    """Drive intake -> classify -> a provisioned worktree, approving the checkpoint."""
    intake = loop.advance(repo, issue_id, inputs=loop.Inputs(work_type="task"))
    assert intake.checkpoint == "classify", intake.detail
    policy.approve_checkpoint(repo, issue_id, "classify")
    return loop.advance(repo, issue_id)


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
        detail="fixture dispatch",
    )


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
    first = _create_bead(repo, "the earlier lane", parent=root)
    second = _create_bead(repo, "the later lane", parent=root)
    # The later lane genuinely depends on the earlier one.
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


def test_a_landing_pass_invents_no_coupling_from_the_shared_tracker(harness_repo: Path) -> None:
    """Every landing rewrites ``.beads/**``; that must not read as a scope collision.

    Pins the second basicly-kjc5.10 shape: ``lstrip("./")`` ate the leading dot
    of ``.beads/``, so the engine-path filter matched nothing and each landing
    attributed a false ``blocks`` edge to the lane that landed before it.
    """
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
    alpha = _create_bead(repo, "lane declaring the shared file", parent=root)
    beta = _create_bead(repo, "lane declaring the shared tree", parent=root)
    for child, scope in ((alpha, "src/shared.py"), (beta, "src/*.py")):
        _tracker(repo, "dep", "add", child, root, "-t", "parent-child")
        _tracker(
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

"""What a supervisor pass does with a coupling it only finds at the landing.

Split out of ``test_integration_loop.py``, alongside ``test_integration_supervisor.py``
which covers a clean pass over two lanes. This half is the unhappy path: a landing
that breaks a sibling lane has to cancel it, tell it why, and leave it free to
re-dispatch; a coupling nobody declared has to reach the graph without retroactively
gating the lane that discovered it; and once discovered it has to gate and order the
bead it names — once, so a later pass over the same record proposes nothing new.

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

import json
import subprocess
import sys
from pathlib import Path

import pytest

from basicly import br, loop, loop_state, merge, policy, runner, supervise, worktree

needs_br = pytest.mark.skipif(
    br.which() is None, reason="the beads tracker (br) is not installed on this machine"
)

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

    _br(repo, "init", "--prefix", "fx")
    _br(repo, "sync", "--flush-only")
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

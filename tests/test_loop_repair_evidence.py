"""What a failed landing tells the repair it briefs (basicly-3oxf0d).

Split out of ``test_loop_repair.py``, which keeps the three properties of repairing in
place; this is the one underneath them — whether the brief carries the gate's own output
or only its name. The defect it pins was measured, not imagined: a landing on
``basicly-6ajmrc`` failed ``pyright-windows``, the brief's ``evidence[0].output`` was
``""``, and the next session re-ran the check by hand to learn the two errors the landing
had already captured for its own unreliable-gate test.

Two halves, and the second is what keeps the first from over-reporting:

- The end-to-end half drives a real failing check through the landing's own gate, so the
  command and the output read back out of the brief are the ones that refused the landing
  rather than strings this test composed.
- The false-positive half asserts that a verdict which is *not* the lane's work failing —
  a collision, an unreliable gate, a foreign one — briefs no evidence at all.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import loop, merge, policy, repair_brief, worktree
from basicly.config import PolicyConfig
from basicly.loop_state import NodeState, WorktreeBinding
from basicly.policy import GateStatus
from basicly.worktree import Session

CONFIG = PolicyConfig(required_gates=("verify",), max_rework=2)

# A check that always fails and prints something recognisable, so the brief's command and
# output are read off a real run rather than invented (basicly-m4zv.6).
FAILING_CHECK = json.dumps([
    "python",
    "-c",
    "import sys; sys.stdout.write('E   assert 1 == 2\\n'); sys.exit(1)",
])


@pytest.fixture(autouse=True)
def _no_tracker_writes(monkeypatch: pytest.MonkeyPatch) -> None:
    """These tests run outside a git repo and must never reach the real tracker."""
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop, "_write", lambda *_a, **_k: SimpleNamespace(stdout="{}"))
    monkeypatch.setattr(loop.rubrics, "load_rubrics", lambda *_a, **_k: [])
    monkeypatch.setattr(policy, "record_rework", lambda *_a, **_k: 1)
    monkeypatch.setattr(loop, "lane_rework_spent", lambda *_a, **_k: 1)
    monkeypatch.setattr(
        policy,
        "record_finding_set",
        lambda _r, _i, _g, found: policy.Convergence(
            policy.PROGRESSING, policy.finding_signature(found), (), 0
        ),
    )


def _landing_worktree(tmp_path: Path, monkeypatch: pytest.MonkeyPatch, checks: str) -> Path:
    """A real worktree declaring *checks*, bound to a build-phase node the loop resumes.

    Real on disk, because the brief is a file in it: a fake path would make the write a
    no-op and every assertion below would pass over nothing.
    """
    path = tmp_path / "wt"
    path.mkdir()
    (path / "basicly.toml").write_text(checks, encoding="utf-8")
    session = Session(
        name="i",
        branch="harness/i",
        base="main",
        base_head="abc",
        worktree_path=str(path),
        created_at="2026-08-07T00:00:00Z",
    )
    monkeypatch.setattr(worktree, "load_session", lambda *_a, **_k: session)
    monkeypatch.setattr(
        loop.loop_state,
        "read_node_state",
        lambda *_a, **_k: NodeState(
            issue_id="i",
            status="in_progress",
            issue_type="task",
            phase="build",
            worktree=WorktreeBinding("i", "harness/i"),
            gates=GateStatus(False, (), (), ("verify",), ()),
            checkpoints=(),
            rework={},
            has_children=False,
        ),
    )
    return path


def test_a_landing_briefs_its_repair_with_the_output_the_gate_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The transcript the landing captured reaches the run that has to fix it.

    The gate runs for real inside the worktree, so what lands in the brief is what
    refused the landing: the check's own argv, and the line it printed.
    """
    checks = f'[[verify.checks]]\nname = "pytest"\ncommand = {FAILING_CHECK}\nmodes = ["full"]\n'
    cwd = _landing_worktree(tmp_path, monkeypatch, checks)
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda root, name, *, bead, verify_mode, **_k: merge._verify_for_landing(
            name, cwd, verify_mode, merge._Landing(root, bead)
        ),
    )

    loop.advance(tmp_path, "i", config=CONFIG, repair_dispatch=False)

    brief = repair_brief.take_repair_brief(cwd)
    assert brief is not None
    assert [(e.check, e.output) for e in brief.evidence] == [("pytest", "E   assert 1 == 2")]
    # The check's own argv, not the whole suite's — that is what the landing ran.
    assert "sys.exit(1)" in brief.evidence[0].command


def test_a_verdict_that_is_not_the_lanes_work_failing_briefs_no_evidence() -> None:
    """A collision, an unreliable gate and a foreign one are not checks a repair re-runs.

    The false-positive half of the criterion: none of the three may have an entry
    fabricated for it, so the whole widening stays keyed on the one status that means the
    lane's own suite went red.
    """
    for result in (
        merge.MergeResult("i", "merge-conflicts", "conflicts in x.py", ("x.py",)),
        merge.MergeResult("i", merge.VERIFY_UNRELIABLE, "passed unchanged on re-run"),
        merge.MergeResult("i", merge.VERIFY_FOREIGN, "invalidated by another lane's record"),
    ):
        assert loop._landing_evidence(result, "full") == ()

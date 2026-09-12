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

FAILING_CHECK = json.dumps([
    "python",
    "-c",
    "import sys; sys.stdout.write('E   assert 1 == 2\\n'); sys.exit(1)",
])


@pytest.fixture(autouse=True)
def _no_tracker_writes(monkeypatch: pytest.MonkeyPatch) -> None:
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
    assert "sys.exit(1)" in brief.evidence[0].command


def test_a_verdict_that_is_not_the_lanes_work_failing_briefs_no_evidence() -> None:

    for result in (
        merge.MergeResult("i", "merge-conflicts", "conflicts in x.py", ("x.py",)),
        merge.MergeResult("i", merge.VERIFY_UNRELIABLE, "passed unchanged on re-run"),
        merge.MergeResult("i", merge.VERIFY_FOREIGN, "invalidated by another lane's record"),
    ):
        assert loop._landing_evidence(result, "full") == ()

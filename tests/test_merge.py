"""Tests for the merge orchestrator (onb.5)."""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, policy, verify
from basicly.config import PolicyConfig
from basicly.worktree import Session


class _Proc:
    def __init__(self, returncode: int = 0, stdout: str = "") -> None:
        self.returncode = returncode
        self.stdout = stdout
        self.stderr = ""


class _FakeGit:
    """Routes git(...) calls by subcommand to canned results, recording them."""

    def __init__(self, responses: dict[str, _Proc]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        return self.responses.get(args[0], _Proc(0))

    def ran(self, subcommand: str) -> bool:
        return any(call[0] == subcommand for call in self.calls)


def _session() -> Session:
    return Session(
        name="feat",
        branch="harness/feat",
        base="main",
        base_head="abc123",
        worktree_path="/tmp/repo.worktrees/feat",
        created_at="2026-07-14T00:00:00Z",
    )


@pytest.fixture
def base_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make load_session/current_branch resolve a clean base checkout on 'main'."""
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")
    monkeypatch.setattr(merge, "reconcile_beads", lambda _r: None)


def test_probe_merge_safe_and_conflicts(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A zero merge-tree exit is SAFE; non-zero surfaces the conflicting paths."""
    monkeypatch.setattr(merge, "git", _FakeGit({"merge-tree": _Proc(0)}))
    assert merge.probe_merge(tmp_path, "main", "harness/feat").safe is True

    monkeypatch.setattr(
        merge, "git", _FakeGit({"merge-tree": _Proc(1, "treeoid\nsrc/a.py\nsrc/b.py")})
    )
    probe = merge.probe_merge(tmp_path, "main", "harness/feat")
    assert probe.safe is False
    assert probe.conflicts == ("src/a.py", "src/b.py")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_happy_path(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """A clean rebase + green verify + safe probe performs the --no-ff merge."""
    fake = _FakeGit({
        "status": _Proc(0, ""),
        "rebase": _Proc(0),
        "merge-tree": _Proc(0),
        "merge": _Proc(0),
        "rev-parse": _Proc(0, "def456"),
    })
    monkeypatch.setattr(merge, "git", fake)
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.merged is True
    merge_calls = [c for c in fake.calls if c[0] == "merge"]
    assert merge_calls and merge_calls[0][:3] == ["merge", "--no-ff", "harness/feat"]


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_aborts_on_rebase_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A rebase conflict aborts cleanly and never reaches the merge."""
    fake = _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(1, "CONFLICT")})
    monkeypatch.setattr(merge, "git", fake)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "rebase-conflicts"
    assert ["rebase", "--abort"] in fake.calls
    assert not fake.ran("merge")


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_blocks_on_failed_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A failing re-verify blocks the merge."""
    monkeypatch.setattr(merge, "git", _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(
        verify,
        "run_verify",
        lambda *_a, **_k: verify.VerifyReport("full", (verify.CheckResult("ruff", "fail", 1),)),
    )
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "verify-failed"


@pytest.mark.usefixtures("base_ready")
def test_merge_worktree_blocks_on_probe_conflict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A conflicting probe blocks the merge even after a clean rebase + verify."""
    monkeypatch.setattr(
        merge,
        "git",
        _FakeGit({"status": _Proc(0, ""), "rebase": _Proc(0), "merge-tree": _Proc(1, "oid\nx.py")}),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")
    assert result.status == "merge-conflicts"


def test_merge_worktree_requires_bead(tmp_path: Path) -> None:
    """A merge without a bead id is rejected (the commit-msg hook needs one)."""
    with pytest.raises(SystemExit, match="bead id"):
        merge.merge_worktree(tmp_path, "feat", bead="")


def test_merge_queue_stops_and_escalates_on_failure(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The queue lands until a failure, then records rework and stops."""
    outcomes = {
        "a": merge.MergeResult("a", "merged", "ok"),
        "b": merge.MergeResult("b", "merge-conflicts", "conflicts in: x.py"),
        "c": merge.MergeResult("c", "merged", "ok"),
    }
    monkeypatch.setattr(merge, "merge_worktree", lambda _r, name, **_kwargs: outcomes[name])
    monkeypatch.setattr(policy, "record_rework", lambda _r, _bead, _gate: 2)

    config = PolicyConfig(required_gates=("verify",), max_rework=2)
    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2"), ("c", "b3")], config=config)

    assert [q.result.name for q in results] == ["a", "b"]  # stopped before "c"
    assert results[0].result.merged is True
    assert results[1].result.merged is False
    assert results[1].attempts == 2
    assert results[1].escalate is True


def test_merge_queue_all_merged(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    """When every node lands, the whole queue is processed with no escalation."""
    monkeypatch.setattr(
        merge,
        "merge_worktree",
        lambda _r, name, **_kwargs: merge.MergeResult(name, "merged", "ok"),
    )
    results = merge.merge_queue(tmp_path, [("a", "b1"), ("b", "b2")])
    assert [q.result.name for q in results] == ["a", "b"]
    assert all(q.result.merged for q in results)

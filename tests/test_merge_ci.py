from __future__ import annotations

from pathlib import Path

import pytest

from basicly import ci_landing, merge, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture
def lane(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")
    _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge-tree": _Proc(0, "merged-tree\n"),
            "merge": _Proc(0),
            "rev-parse": _Proc(0, "def456"),
            "merge-base": _Proc(0),
        }),
    )
    modes: list[str] = []

    def run_verify(_root: Path, mode: str, *_a: object, **_k: object) -> verify.VerifyReport:
        modes.append(mode)
        return verify.VerifyReport(mode, ())

    monkeypatch.setattr(verify, "run_verify", run_verify)
    monkeypatch.setattr(ci_landing, "local_checks", lambda _w: verify.VerifyReport("full", ()))
    return modes


def test_a_ci_landing_merges_on_the_proof_without_a_local_full_verify(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, lane: list[str]
) -> None:
    proved: list[ci_landing.Candidate] = []
    monkeypatch.setattr(
        ci_landing, "prove", lambda _root, candidate: proved.append(candidate) or {}
    )

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert result.merged
    assert lane == []
    assert proved == [ci_landing.Candidate("feat", "basicly-onb.5", "abc123", "def456")]


@pytest.mark.usefixtures("lane")
def test_a_refused_proof_lands_nothing_and_names_the_run(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    def refuse(_root: Path, _candidate: ci_landing.Candidate) -> dict[str, str]:
        raise ci_landing.CiLandingError("quality-gates.yml run R ended failure in shard 3/12")

    monkeypatch.setattr(ci_landing, "prove", refuse)

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert not result.merged
    assert result.status == "verify-failed"
    assert "ended failure in shard 3/12" in result.detail


@pytest.mark.usefixtures("lane")
def test_failing_local_only_checks_stop_a_ci_landing_before_any_push(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setattr(
        ci_landing,
        "local_checks",
        lambda _w: verify.VerifyReport("full", (verify.CheckResult("ledger-fsck", "fail", 1),)),
    )
    monkeypatch.setattr(ci_landing, "prove", lambda *_a: pytest.fail("pushed after a failure"))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5", verify_mode="ci")

    assert result.status == "verify-failed"
    assert "local-only checks failed" in result.detail

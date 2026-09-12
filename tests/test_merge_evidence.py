from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _FAILED, _GREEN, _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture(autouse=True)
def _base_ready(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


def _landing(monkeypatch: pytest.MonkeyPatch, tmp_path: Path, rerun: verify.VerifyReport):
    _patch_git(monkeypatch, _FakeGit({**_HAS_WORK, "status": _Proc(0, ""), "rebase": _Proc(0)}))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: _FAILED)
    monkeypatch.setattr(verify, "rerun_failures", lambda *_a, **_k: rerun)
    return merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")


def test_a_reproduced_failure_carries_the_checks_the_re_run_captured(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    reproduced = verify.VerifyReport(
        "full",
        (
            verify.CheckResult(
                "pyright", "fail", 1, output="src/a.py:1: error", command=("pyright", "--strict")
            ),
            verify.CheckResult("ruff", "pass", 0, command=("ruff", "check")),
        ),
    )

    result = _landing(monkeypatch, tmp_path, reproduced)

    assert result.status == "verify-failed"
    assert [(c.name, c.output, c.command) for c in result.checks] == [
        ("pyright", "src/a.py:1: error", ("pyright", "--strict"))
    ]


def test_an_unreliable_verdict_carries_no_checks_at_all(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    result = _landing(monkeypatch, tmp_path, _GREEN)

    assert result.unreliable is True
    assert result.checks == ()

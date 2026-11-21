from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _landing_git, _patch_git, _Proc, base_ready

__all__ = ["base_ready"]


@pytest.mark.usefixtures("base_ready")
def test_a_refused_landing_merge_names_what_git_said(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    hook = f"{Path.home()}/repo/.git/hooks/commit-msg"
    said = f"ERROR: commit-msg refused the subject\n  at {hook}\n"
    _patch_git(monkeypatch, _landing_git(merge=_Proc(1, "", said)))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.MERGE_UNPROVEN
    assert "exited 1; git said: ERROR: commit-msg refused the subject" in result.detail
    assert str(Path.home()) not in result.detail
    assert "preserve checkout and index for inspection" in result.detail


@pytest.mark.usefixtures("base_ready")
def test_a_silent_refused_landing_merge_says_git_printed_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _landing_git(merge=_Proc(1, "", "")))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))

    result = merge.merge_worktree(tmp_path, "feat", bead="basicly-onb.5")

    assert result.status == merge.MERGE_UNPROVEN
    assert "exited 1; git printed nothing;" in result.detail

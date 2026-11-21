from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import merge, process_confirmation, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session

if TYPE_CHECKING:
    import pytest


def test_a_landing_never_reads_tracker_confirmation_evidence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    bead = "demo-1"
    fake = _patch_git(
        monkeypatch,
        _FakeGit({
            **_HAS_WORK,
            "status": _Proc(0, ""),
            "rebase": _Proc(0),
            "merge": _Proc(0),
            "rev-parse": _Proc(0, "landed"),
            "merge-base": _Proc(0),
        }),
    )
    monkeypatch.setattr(merge, "load_session", lambda *_: _session())
    monkeypatch.setattr(merge, "current_branch", lambda *_: "main")
    monkeypatch.setattr(merge, "known_bead_ids", lambda *_: {bead})
    report = verify.VerifyReport(
        "full",
        (verify.CheckResult("actual", "pass", 0, output="observed result", command=("check",)),),
    )
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: report)
    read: list[str] = []

    def evidence(*_args: object) -> tuple[dict[str, object], bool]:
        read.append("confirmation_evidence")
        return {"owed": ["missing-check"]}, True

    monkeypatch.setattr(process_confirmation.tracker, "confirmation_evidence", evidence)

    result = merge.merge_worktree(tmp_path, "feat", bead=bead)

    assert result.merged is True
    assert fake.ran("merge") is True
    assert read == []

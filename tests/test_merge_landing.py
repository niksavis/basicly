from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture(autouse=True)
def _base_ready(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


_RELEASE_NOTES_CONFIG = """\
[[verify.checks]]
name = "release-notes"
command = ["uv", "run", "python", ".scripts/check_release_notes.py"]
modes = ["fast", "full"]
"""

_BEAD = "basicly-85cadb"

_OWED = (
    "release-notes: basicly-85cadb: declares a shipped path in `## Scope` and holds no "
    "release note; ship closes it and removes this worktree before the closing commit is "
    "refused"
    "\n"
    "release-notes:   write `changelog.d/basicly-85cadb.<category>.md`, or declare it "
    "invisible to a consumer in [tool.release_notes.invisible] with its reason and record "
    "`count_delta = +1` under [ratchet.release_notes] in basicly.d/<bead-id>.toml"
    "\n"
)


_MERGES_CLEAN = {
    **_HAS_WORK,
    "status": _Proc(0, ""),
    "rebase": _Proc(0),
    "merge-tree": _Proc(0),
    "merge": _Proc(0),
    "rev-parse": _Proc(0, "def456"),
    "merge-base": _Proc(0),
}


def _wired_gate(
    monkeypatch: pytest.MonkeyPatch, repo_root: Path, result: verify.CheckResult
) -> tuple[list[tuple[tuple[str, ...], Path]], _FakeGit]:

    (repo_root / "basicly.toml").write_text(_RELEASE_NOTES_CONFIG, encoding="utf-8")
    fake = _patch_git(monkeypatch, _FakeGit(dict(_MERGES_CLEAN)))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    asked: list[tuple[tuple[str, ...], Path]] = []

    def _run_check(check, cwd, _mode, **_kwargs):
        asked.append((check.command, cwd))
        return result

    monkeypatch.setattr(verify, "run_check", _run_check)
    return asked, fake


def test_a_landing_is_refused_before_the_merge_when_the_lane_owes_a_release_note(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked, fake = _wired_gate(
        monkeypatch, tmp_path, verify.CheckResult("release-notes", "fail", 1, output=_OWED)
    )

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert result.status == "verify-failed"
    assert f"changelog.d/{_BEAD}.<category>.md" in result.detail
    assert "invisible to a consumer" in result.detail
    assert result.detail.endswith("basicly.d/<bead-id>.toml")
    assert asked, "the landing never asked the gate"
    assert not fake.ran("merge"), "base was merged over a debt the landing had already found"


def test_the_landing_asks_the_release_note_gate_about_this_lanes_own_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    asked, _ = _wired_gate(monkeypatch, tmp_path, verify.CheckResult("release-notes", "pass", 0))

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert asked == [
        (
            (
                "uv",
                "run",
                "python",
                ".scripts/check_release_notes.py",
                "--landing",
                _BEAD,
            ),
            _session().path,
        )
    ]
    assert result.status == "merged"


def test_a_gate_that_fails_silently_still_refuses_the_landing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _wired_gate(monkeypatch, tmp_path, verify.CheckResult("release-notes", "fail", 1))

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert result.status == "verify-failed"
    assert "printed nothing" in result.detail


def test_a_tree_that_declares_no_release_notes_check_is_not_in_debt_to_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    _patch_git(monkeypatch, _FakeGit(dict(_MERGES_CLEAN)))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "run_check", lambda *_a, **_k: pytest.fail("asked a gate"))

    assert merge.merge_worktree(tmp_path, "feat", bead=_BEAD).status == "merged"

"""Tests for the landing's release-note refusal (basicly-18iz59).

`release-notes` judges *closed* records and a landing lane's record is still open, so the
suite passed at every landing and the refusal arrived on the commit that closes the record
— after ship had torn the worktree down. Two lanes ended there (basicly-ibzr0f,
basicly-mcf2uh) and a human declared both invisible on main.

Its own file rather than an addition to `test_merge.py`: the subject is one step of a
landing and the fixtures it needs (a repo that declares the check, a stubbed answer from
it) are shared by nothing else there.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import merge, verify
from tests.test_merge import _HAS_WORK, _FakeGit, _patch_git, _Proc, _session


@pytest.fixture(autouse=True)
def _base_ready(monkeypatch: pytest.MonkeyPatch) -> None:
    """Resolve a clean base checkout on 'main', as every landing test needs.

    Declared here rather than imported: `test_merge.py`'s copy is a fixture, and a fixture
    reaches only the module that defines it.
    """
    monkeypatch.setattr(merge, "load_session", lambda _n, _r: _session())
    monkeypatch.setattr(merge, "current_branch", lambda _r: "main")


_RELEASE_NOTES_CONFIG = """\
[[verify.checks]]
name = "release-notes"
command = ["uv", "run", "python", ".scripts/check_release_notes.py"]
modes = ["fast", "full"]
"""

# What `uv run python .scripts/check_release_notes.py --landing basicly-85cadb` actually
# printed in this checkout, not a paraphrase of it: the landing reports through
# `verify.check_remedy`, which caps detail-plus-remedy at 400 characters, and a fixture
# written short would pass while the real refusal lost its second remedy to the cap.
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


# Everything after the gate answered green, so a pass reaches the --no-ff merge.
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
    """A repo whose `release-notes` check is configured, with its landing answer stubbed.

    Returns what the stub was asked — so a test reads the argv the landing used rather than
    only its verdict — and the git stub, so a test can assert base was never merged.
    """
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
    """A green suite is not enough, and base must be left untouched."""
    asked, fake = _wired_gate(
        monkeypatch, tmp_path, verify.CheckResult("release-notes", "fail", 1, output=_OWED)
    )

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert result.status == "verify-failed"
    assert f"changelog.d/{_BEAD}.<category>.md" in result.detail
    assert "invisible to a consumer" in result.detail
    # The tail: `check_remedy` truncates at 400 characters, and the first wording of this
    # finding spent that budget on its detail and lost the declaration half to the cap.
    assert result.detail.endswith("basicly.d/<bead-id>.toml")
    assert asked, "the landing never asked the gate"
    assert not fake.ran("merge"), "base was merged over a debt the landing had already found"


def test_the_landing_asks_the_release_note_gate_about_this_lanes_own_record(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The lane's id is the whole discriminator: every other record is somebody else's."""
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
    """An empty answer must not read as "owes nothing" — that is a failure taken for a pass."""
    _wired_gate(monkeypatch, tmp_path, verify.CheckResult("release-notes", "fail", 1))

    result = merge.merge_worktree(tmp_path, "feat", bead=_BEAD)

    assert result.status == "verify-failed"
    assert "printed nothing" in result.detail


def test_a_tree_that_declares_no_release_notes_check_is_not_in_debt_to_it(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A consumer repo without this gate must not have every landing refused by it."""
    _patch_git(monkeypatch, _FakeGit(dict(_MERGES_CLEAN)))
    monkeypatch.setattr(verify, "run_verify", lambda *_a, **_k: verify.VerifyReport("full", ()))
    monkeypatch.setattr(verify, "run_check", lambda *_a, **_k: pytest.fail("asked a gate"))

    assert merge.merge_worktree(tmp_path, "feat", bead=_BEAD).status == "merged"

"""``rebase.refresh_generated``: rebuild a declared artifact the rebase left stale.

Its sibling ``rebuild_generated_conflicts`` fires on a *conflict*, and that is the case
``tests/test_rebase.py`` covers. Staleness needs no conflict, which is why these live
apart: a lane that only adds a module leaves ``plan-current-state`` counting a tree that
no longer exists, and the path is outside every lane's scope (basicly-e2mz.35).

A separate module rather than an addition to ``test_rebase.py``, which sits exactly at
its 4000-token size baseline.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from basicly import rebase

if TYPE_CHECKING:
    import pytest

_BEAD = "basicly-e2mz.35"
_PATH = "docs/architecture/status.md"


class _Proc:
    def __init__(self, returncode: int = 0) -> None:
        self.returncode = returncode
        self.stdout = ""
        self.stderr = ""


class _FakeGit:
    """Routes git(...) by whole argument list then subcommand, recording every call.

    An unstubbed subcommand raises rather than succeeding, because a blanket success
    answers "did the rebuild commit?" in the affirmative and makes the assertions
    vacuous.
    """

    def __init__(self, responses: dict[str, _Proc]) -> None:
        self.responses = responses
        self.calls: list[list[str]] = []

    def __call__(self, args, **_kwargs):
        self.calls.append(args)
        for key in (" ".join(args), args[0]):
            if key in self.responses:
                return self.responses[key]
        raise AssertionError(f"unstubbed git subcommand {args[0]!r}: git {' '.join(args)}")

    def message(self) -> str:
        """The `-m` argument of the commit this fake recorded."""
        commit = next(args for args in self.calls if args[0] == "commit")
        return commit[commit.index("-m") + 1]


def _declare(repo_root: Path) -> None:
    """Declare :data:`_PATH` rebuildable by a command that always succeeds."""
    (repo_root / "basicly.toml").write_text(
        f'[worktree.regenerate_commands]\n"{_PATH}" = ["true"]\n', encoding="utf-8"
    )


def _patch(monkeypatch: pytest.MonkeyPatch, diff: int) -> _FakeGit:
    """Patch the module's ``run`` to succeed and ``git`` to report *diff* for the path."""
    monkeypatch.setattr(rebase, "run", lambda *_a, **_k: _Proc(0))
    fake = _FakeGit({
        f"diff --quiet -- {_PATH}": _Proc(diff),
        "add": _Proc(0),
        "commit": _Proc(0),
    })
    monkeypatch.setattr(rebase, "git", fake)
    return fake


def test_an_artifact_the_rebase_left_stale_is_rebuilt_and_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A changed artifact is staged and committed, so it reaches the base.

    The landing merges the branch, not the worktree, so leaving the rebuild uncommitted
    would pass the verify that follows and then never land.
    """
    _declare(tmp_path)
    fake = _patch(monkeypatch, diff=1)

    assert rebase.refresh_generated(tmp_path, tmp_path, _BEAD) == (_PATH,)
    assert ["add", "--", _PATH] in fake.calls
    assert _BEAD in fake.message()


def test_an_artifact_the_rebuild_did_not_change_is_not_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """No diff means no commit: a landing must not grow an empty chore commit per lane."""
    _declare(tmp_path)
    fake = _patch(monkeypatch, diff=0)

    assert rebase.refresh_generated(tmp_path, tmp_path, _BEAD) == ()
    assert not [args for args in fake.calls if args[0] == "commit"]


def test_nothing_declared_rebuilds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """A repository declaring no generated path is untouched, not an error."""
    (tmp_path / "basicly.toml").write_text("[worktree]\n", encoding="utf-8")
    fake = _patch(monkeypatch, diff=1)

    assert rebase.refresh_generated(tmp_path, tmp_path, _BEAD) == ()
    assert fake.calls == []

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
        commit = next(args for args in self.calls if args[0] == "commit")
        return commit[commit.index("-m") + 1]


def _declare(repo_root: Path) -> None:
    (repo_root / "basicly.toml").write_text(
        f'[worktree.regenerate_commands]\n"{_PATH}" = ["true"]\n', encoding="utf-8"
    )


def _patch(monkeypatch: pytest.MonkeyPatch, diff: int) -> _FakeGit:
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

    _declare(tmp_path)
    fake = _patch(monkeypatch, diff=1)

    assert rebase.refresh_generated(tmp_path, tmp_path, _BEAD) == (_PATH,)
    assert ["add", "--", _PATH] in fake.calls
    assert _BEAD in fake.message()


def test_an_artifact_the_rebuild_did_not_change_is_not_committed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _declare(tmp_path)
    fake = _patch(monkeypatch, diff=0)

    assert rebase.refresh_generated(tmp_path, tmp_path, _BEAD) == ()
    assert not [args for args in fake.calls if args[0] == "commit"]


def test_nothing_declared_rebuilds_nothing(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "basicly.toml").write_text("[worktree]\n", encoding="utf-8")
    fake = _patch(monkeypatch, diff=1)

    assert rebase.refresh_generated(tmp_path, tmp_path, _BEAD) == ()
    assert fake.calls == []

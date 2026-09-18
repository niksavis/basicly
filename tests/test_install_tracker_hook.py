from __future__ import annotations

import argparse
import sys
from pathlib import Path

import pytest

from basicly import cli
from basicly.scaffolds import UVX_COMMAND

INSTALLER = cli.TRACKER_HOOK_INSTALLER


def _repo(tmp_path: Path, *, vendored: bool) -> Path:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    if vendored:
        target = tmp_path / INSTALLER
        target.parent.mkdir(parents=True)
        source = Path(".basicly/core/kit/tracker/install_hook.py").read_text(encoding="utf-8")
        target.write_text(source, encoding="utf-8")
        (tmp_path / ".basicly" / "core" / "kit" / "tracker" / "cli.py").write_text(
            "", encoding="utf-8"
        )
    return tmp_path


@pytest.fixture
def rooted(monkeypatch: pytest.MonkeyPatch):
    def _at(repo: Path) -> None:
        monkeypatch.setattr(cli, "_repo_root", lambda: repo)

    return _at


def test_an_absent_kit_leaves_the_hook_unwired(tmp_path: Path, rooted, capsys) -> None:
    rooted(_repo(tmp_path, vendored=False))

    assert cli.cmd_tracker_hook(argparse.Namespace()) == 0

    assert not (tmp_path / ".git" / "hooks" / "post-merge").exists()
    assert "no merge folds the pending shards" in capsys.readouterr().out


def test_the_hook_resolves_the_engine_at_run_time_not_install_time(tmp_path: Path, rooted) -> None:
    rooted(_repo(tmp_path, vendored=True))

    assert cli.cmd_tracker_hook(argparse.Namespace()) == 0

    written = (tmp_path / ".git" / "hooks" / "post-merge").read_text(encoding="utf-8")
    assert "command -v basicly" in written
    assert "basicly tracker fold" in written
    assert UVX_COMMAND in written
    assert str(Path(sys.executable).parent) not in written


def test_the_hook_advises_the_pinned_command_a_consumer_can_always_run(
    tmp_path: Path, rooted
) -> None:
    rooted(_repo(tmp_path, vendored=True))

    cli.cmd_tracker_hook(argparse.Namespace())

    written = (tmp_path / ".git" / "hooks" / "post-merge").read_text(encoding="utf-8")
    advice = written.split("are not folded; run ", 1)[1].split("'", 1)[0]
    assert advice == f"{UVX_COMMAND} {cli.FOLD_COMMAND}"


def test_the_hook_calls_the_engine_and_not_the_kit(tmp_path: Path, rooted) -> None:
    rooted(_repo(tmp_path, vendored=True))

    assert cli.cmd_tracker_hook(argparse.Namespace()) == 0

    written = (tmp_path / ".git" / "hooks" / "post-merge").read_text(encoding="utf-8")
    assert "git commit" not in written
    assert "compact" not in written.replace("basicly-tracker compact", "")


def test_install_runs_the_tracker_hook_step() -> None:
    source = Path("src/basicly/cli.py").read_text(encoding="utf-8")

    steps = source.split("steps: list[tuple[str, Any, argparse.Namespace]] = [", 1)[1]
    assert "tracker-hook" in steps.split("]\n", 1)[0]

from __future__ import annotations

import argparse
import io
import sys
from pathlib import Path

import pytest

from basicly import cli
from basicly.scaffolds import UVX_COMMAND
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

INSTALLER = cli.TRACKER_HOOK_INSTALLER


def _repo(tmp_path: Path, *, vendored: bool, folds: bool = True) -> Path:
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    if folds:
        (tmp_path / "basicly.toml").write_text("[tracker]\nfold_on_merge = true\n", "utf-8")
    if vendored:
        target = tmp_path / INSTALLER
        target.parent.mkdir(parents=True)
        for name in ("install_hook.py", "import_offer.py"):
            source = (REPO_ROOT / KIT_RELATIVE / name).read_text(encoding="utf-8")
            target.with_name(name).write_text(source, encoding="utf-8")
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


def test_a_dry_run_previews_with_the_kit_the_sync_would_install(tmp_path: Path) -> None:
    stale = tmp_path / cli.TRACKER_HOOK_INSTALLER
    stale.parent.mkdir(parents=True)
    stale.write_text("", encoding="utf-8")

    previewed = cli._installer(tmp_path, cli.TRACKER_HOOK_INSTALLER, dry_run=True)
    real = cli._installer(tmp_path, cli.TRACKER_HOOK_INSTALLER, dry_run=False)

    assert previewed != stale, "a dry run that reads the vendored kit reports the old kit's flags"
    assert previewed.read_text(encoding="utf-8").count("--advice") >= 1
    assert real == stale


def test_a_real_run_uses_the_vendored_kit_even_when_it_differs(tmp_path: Path) -> None:
    vendored = tmp_path / cli.TRACKER_HOOK_INSTALLER
    vendored.parent.mkdir(parents=True)
    vendored.write_text("# a consumer's own copy\n", encoding="utf-8")

    assert cli._installer(tmp_path, cli.TRACKER_HOOK_INSTALLER, dry_run=False) == vendored


def test_a_host_command_needs_no_in_repo_kit_path(tmp_path: Path) -> None:
    outside = _load(REPO_ROOT / KIT_RELATIVE / "install_hook.py", "kit_hook_outside")
    (tmp_path / ".git" / "hooks").mkdir(parents=True)
    stream = io.StringIO()

    assert (
        outside.install(
            tmp_path,
            ledger=tmp_path / ".basicly" / "ledger",
            dry_run=False,
            interpreter="RUNNER",
            stream=stream,
            command="basicly tracker fold",
        )
        == 0
    )

    assert "basicly tracker fold" in (tmp_path / ".git" / "hooks" / "post-merge").read_text(
        encoding="utf-8"
    )


def test_a_repository_that_does_not_opt_in_gets_no_fold(tmp_path: Path, rooted, capsys) -> None:
    rooted(_repo(tmp_path, vendored=True, folds=False))

    assert cli.cmd_tracker_hook(argparse.Namespace()) == 0

    assert not (tmp_path / ".git" / "hooks" / "post-merge").exists()
    assert "no post-merge fold is wired" in capsys.readouterr().out


def test_opting_out_removes_a_fold_already_wired(tmp_path: Path, rooted) -> None:
    repo = _repo(tmp_path, vendored=True)
    rooted(repo)
    cli.cmd_tracker_hook(argparse.Namespace())
    (repo / "basicly.toml").write_text("[tracker]\nfold_on_merge = false\n", "utf-8")

    assert cli.cmd_tracker_hook(argparse.Namespace()) == 0

    assert not (repo / ".git" / "hooks" / "post-merge").exists()


def test_basicly_install_offers_a_beads_backlog_and_imports_nothing(
    tmp_path: Path, rooted, capsys
) -> None:
    repo = _repo(tmp_path, vendored=True)
    (repo / ".beads").mkdir()
    (repo / ".beads" / "issues.jsonl").write_text('{"id": "old-a1"}\n{"id": "old-b2"}\n', "utf-8")
    rooted(repo)

    assert cli.cmd_tracker_hook(argparse.Namespace()) == 0

    out = capsys.readouterr().out
    assert "found a beads backlog: .beads/issues.jsonl holds 2 record(s)" in out
    assert "run `basicly tracker import .beads/issues.jsonl --from beads`" in out
    assert "lands in refine" in out
    assert not list((repo / ".basicly" / "ledger").glob("*.jsonl"))

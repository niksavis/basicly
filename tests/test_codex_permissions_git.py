from __future__ import annotations

import os
import subprocess
import sys
import tomllib
from pathlib import Path

from basicly import codex_permissions, runner
from tests.markdownlint_runtime_fixture import provision_markdownlint_double
from tests.test_codex_permissions import (
    GENERATED_USER_CONFIG,
    config,
    generated,
    repo,
    rules,
)


def _basicly(root: Path, home: Path, *args: str) -> subprocess.CompletedProcess[str]:
    source = Path(__file__).resolve().parents[1] / "src"
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=root,
        env={**os.environ, "CODEX_HOME": str(home), "PYTHONPATH": str(source)},
        capture_output=True,
        text=True,
        check=False,
    )


def test_permissions_build_removes_the_policy_and_the_check_then_passes(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root)
    home = tmp_path / "codex-home"

    assert _basicly(root, home, "permissions-check").returncode == 1

    build = _basicly(root, home, "permissions-build")

    assert build.returncode == 0, build.stderr
    assert ".codex/config.toml" in build.stdout
    assert _basicly(root, home, "permissions-check").returncode == 0
    assert not config(root).exists()


def test_install_removes_the_policy_and_replays_without_a_change(tmp_path: Path) -> None:
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    provision_markdownlint_double(consumer)
    generated(consumer)
    home = tmp_path / "codex-home"
    home.mkdir()
    user = home / "config.toml"
    user.write_text(GENERATED_USER_CONFIG, encoding="utf-8")

    installed = _basicly(consumer, home, "install", "--codex-user")

    assert installed.returncode == 0, installed.stderr
    assert not config(consumer).exists()
    assert not rules(consumer).exists()
    assert tomllib.loads(user.read_text(encoding="utf-8")) == {"default_permissions": ":workspace"}
    before = user.read_bytes()

    repeated = _basicly(consumer, home, "install", "--codex-user")

    assert repeated.returncode == 0, repeated.stderr
    assert user.read_bytes() == before
    assert len(list((home / "permission-backups").glob("*.toml"))) == 1


def test_the_decider_runner_selects_a_codex_built_in_profile(tmp_path: Path) -> None:
    root = repo(tmp_path / "app")
    generated(root)
    codex_permissions.remove_permissions(root)

    spec = next(item for item in runner.BUILTIN_RUNNERS if item.name == "codex")
    confined = runner.confine_for_decider(spec)

    assert confined is not None
    selection = runner.format_command(confined, "judge")[-1]
    assert not any(
        codex_permissions.is_generated_profile(name)
        for name in (codex_permissions.PROFILE_NAME, codex_permissions.READ_ONLY_PROFILE_NAME)
        if name in selection
    )
    assert selection == 'default_permissions=":read-only"'

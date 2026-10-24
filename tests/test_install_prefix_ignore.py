from __future__ import annotations

import os
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path

import pytest

from basicly import cli, config, owned_store
from tests.test_tracker_prefix_home import engine_repo

REPO_ROOT = Path(__file__).resolve().parents[1]
TEMPLATE = owned_store.LEDGER_DIR / owned_store.TEMPLATE_FILE
LEGACY_CONFIG = b'[tracker]\nmode = "owned"\nprefix = "acme"\n'


def _git(root: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(["git", *args], cwd=root, text=True, capture_output=True, check=True)


def _consumer(root: Path, ignore: str) -> Path:
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text(ignore, encoding="utf-8")
    (root / config.CONFIG_FILE).write_bytes(LEGACY_CONFIG)
    return root


def _install(root: Path, *, dry_run: bool = False) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [
            sys.executable,
            "-m",
            "basicly.cli",
            "install",
            "--technologies",
            "python",
            *(["--dry-run"] if dry_run else []),
        ],
        cwd=root,
        env={**os.environ, "PYTHONPATH": str(REPO_ROOT / "src")},
        text=True,
        capture_output=True,
        check=False,
    )


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize("ignore", ["*.json\n", ".basicly/\n", ".basicly/ledger/\n"])
def test_install_refuses_an_ignored_prefix_destination_before_any_write(
    tmp_path: Path, dry_run: bool, ignore: str
) -> None:
    root = _consumer(tmp_path / "consumer", ignore)

    result = _install(root, dry_run=dry_run)

    assert result.returncode != 0, result.stdout + result.stderr
    assert "ignored" in result.stderr
    assert TEMPLATE.as_posix() in result.stderr
    assert f".gitignore:1:{ignore.strip()}" in result.stderr
    assert "!/.basicly/ledger/template.json" in result.stderr
    assert (root / config.CONFIG_FILE).read_bytes() == LEGACY_CONFIG
    assert (root / ".gitignore").read_text(encoding="utf-8") == ignore
    assert not (root / ".basicly").exists()
    assert not (root / ".basicly-local").exists()


def test_an_existing_ignored_template_is_preserved_when_install_refuses(
    tmp_path: Path,
) -> None:
    root = _consumer(tmp_path / "consumer", "*.json\n")
    template = root / TEMPLATE
    template.parent.mkdir(parents=True)
    before = b'{ "prefix": "acme", "mode": "extend" }\n'
    template.write_bytes(before)

    result = _install(root)

    assert result.returncode != 0, result.stdout + result.stderr
    assert template.read_bytes() == before
    assert (root / config.CONFIG_FILE).read_bytes() == LEGACY_CONFIG
    assert not (root / ".basicly/core").exists()


@pytest.mark.parametrize("ignore", ["*.json\n", "*\n", ".basicly/\n"])
def test_the_reported_root_negations_allow_install_to_migrate_the_prefix(
    tmp_path: Path, ignore: str
) -> None:
    root = _consumer(tmp_path / "consumer", ignore)
    refused = _install(root)
    assert refused.returncode != 0
    negations = [line for line in refused.stderr.splitlines() if line.startswith("!/")]
    assert negations
    (root / ".gitignore").write_text(ignore + "\n".join(negations) + "\n", encoding="utf-8")

    installed = _install(root)

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert config.legacy_tracker_prefix(root) is None
    assert '"prefix": "acme"' in (root / TEMPLATE).read_text(encoding="utf-8")


def test_a_nested_ignore_reports_negations_relative_to_its_own_directory(
    tmp_path: Path,
) -> None:
    root = _consumer(tmp_path / "consumer", "")
    ledger = root / owned_store.LEDGER_DIR
    ledger.mkdir(parents=True)
    nested = ledger / ".gitignore"
    nested.write_text("*.json\n", encoding="utf-8")
    refused = _install(root)
    assert refused.returncode != 0
    assert ".basicly/ledger/.gitignore:1:*.json" in refused.stderr
    assert "!/template.json" in refused.stderr
    nested.write_text("*.json\n!/template.json\n", encoding="utf-8")

    installed = _install(root)

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert config.legacy_tracker_prefix(root) is None


def test_an_already_tracked_template_is_not_refused_by_a_broad_ignore_rule(
    tmp_path: Path,
) -> None:
    root = _consumer(tmp_path / "consumer", "*.json\n")
    template = root / TEMPLATE
    template.parent.mkdir(parents=True)
    template.write_text('{"prefix":"acme"}\n', encoding="utf-8")
    _git(root, "add", "--force", TEMPLATE.as_posix())

    result = _install(root)

    assert result.returncode == 0, result.stdout + result.stderr
    assert config.legacy_tracker_prefix(root) is None
    assert template.read_text(encoding="utf-8") == '{"prefix":"acme"}\n'


def test_a_repository_exclusion_reports_a_repository_ignore_remedy(tmp_path: Path) -> None:
    root = _consumer(tmp_path / "consumer", "")
    (root / ".git/info/exclude").write_text("*.json\n", encoding="utf-8")
    refused = _install(root)
    assert refused.returncode != 0
    assert ".git/info/exclude:1:*.json" in refused.stderr
    assert "end of .gitignore" in refused.stderr
    negations = [line for line in refused.stderr.splitlines() if line.startswith("!/")]
    assert negations
    (root / ".gitignore").write_text("\n".join(negations) + "\n", encoding="utf-8")

    installed = _install(root)

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert config.legacy_tracker_prefix(root) is None


@pytest.mark.parametrize("nested", [False, True])
def test_install_in_a_git_subdirectory_reports_a_usable_ignore_remedy(
    tmp_path: Path, nested: bool
) -> None:
    repository = _consumer(tmp_path / "repository", "")
    root = repository / "project"
    root.mkdir()
    (root / config.CONFIG_FILE).write_bytes(LEGACY_CONFIG)
    ignore = root / ".basicly/ledger/.gitignore" if nested else repository / ".gitignore"
    ignore.parent.mkdir(parents=True, exist_ok=True)
    ignore.write_text("*.json\n", encoding="utf-8")
    refused = _install(root)

    assert refused.returncode != 0
    assert "ignored" in refused.stderr
    assert (".basicly/ledger/.gitignore" if nested else "../.gitignore") in refused.stderr
    negations = [line for line in refused.stderr.splitlines() if line.startswith("!/")]
    assert negations
    ignore.write_text("*.json\n" + "\n".join(negations) + "\n", encoding="utf-8")

    installed = _install(root)

    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert config.legacy_tracker_prefix(root) is None


@pytest.mark.parametrize("operation", [config.retire_legacy_tracker_prefix, cli._setup_tracker])
def test_direct_prefix_migration_refuses_an_ignored_destination_without_writes(
    tmp_path: Path, operation: Callable[[Path], object]
) -> None:
    root = engine_repo(tmp_path, 'prefix = "acme"\n')
    _git(root, "init", "-q", "-b", "main")
    (root / ".gitignore").write_text("*.json\n", encoding="utf-8")

    with pytest.raises((ValueError, SystemExit), match="ignored"):
        operation(root)

    assert (root / config.CONFIG_FILE).read_bytes() == LEGACY_CONFIG
    assert not (root / TEMPLATE).exists()

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests.markdownlint_runtime_fixture import (
    copy_locked_markdownlint_runtime,
    provision_markdownlint_double,
)
from tests.test_cli import run_basicly_consumer


def _files(root: Path) -> dict[str, bytes]:
    return {
        path.relative_to(root).as_posix(): path.read_bytes()
        for path in root.rglob("*")
        if path.is_file()
    }


@pytest.mark.parametrize("dry_run", [False, True])
def test_install_refuses_missing_markdownlint_before_writing(tmp_path: Path, dry_run: bool) -> None:
    (tmp_path / "keep.txt").write_text("consumer data\n", encoding="utf-8")
    before = _files(tmp_path)

    result = run_basicly_consumer(tmp_path, "install", *(["--dry-run"] if dry_run else []))

    assert result.returncode != 0
    assert "npm install --save-dev markdownlint-cli2" in result.stderr
    assert _files(tmp_path) == before


@pytest.mark.parametrize("dry_run", [False, True])
@pytest.mark.parametrize(
    "filename", ["basicly.toml", "basicly.d/20-hooks.toml", "basicly.local.toml"]
)
def test_explicit_markdownlint_optout_allows_install(
    tmp_path: Path, dry_run: bool, filename: str
) -> None:
    configuration = tmp_path / filename
    configuration.parent.mkdir(parents=True, exist_ok=True)
    original = b"[hooks.markdownlint]\nenabled=false\n"
    configuration.write_bytes(original)
    before = _files(tmp_path)

    result = run_basicly_consumer(tmp_path, "install", *(["--dry-run"] if dry_run else []))

    assert result.returncode == 0, result.stdout + result.stderr
    assert "opted out" in result.stdout
    assert configuration.read_bytes() == original
    if dry_run:
        assert _files(tmp_path) == before
    else:
        assert (tmp_path / ".basicly/core/hooks/markdownlint.py").is_file()


@pytest.mark.parametrize("dry_run", [False, True])
def test_non_node_technology_selection_needs_no_markdown_runtime(
    tmp_path: Path, dry_run: bool
) -> None:
    result = run_basicly_consumer(
        tmp_path, "install", "--technologies", "python", *(["--dry-run"] if dry_run else [])
    )

    assert result.returncode == 0, result.stdout + result.stderr
    assert not (tmp_path / "node_modules").exists()
    if not dry_run:
        assert "markdownlint" not in (tmp_path / ".basicly/lefthook.yml").read_text(
            encoding="utf-8"
        )


@pytest.mark.parametrize("dry_run", [False, True])
def test_install_refuses_malformed_markdownlint_configuration_without_writes(
    tmp_path: Path, dry_run: bool
) -> None:
    (tmp_path / "basicly.toml").write_text(
        '[hooks.markdownlint]\nenabled="false"\n', encoding="utf-8"
    )
    before = _files(tmp_path)

    result = run_basicly_consumer(tmp_path, "install", *(["--dry-run"] if dry_run else []))

    assert result.returncode != 0
    assert "hooks.markdownlint.enabled" in result.stderr
    assert _files(tmp_path) == before


def test_enabled_install_and_real_linter_accept_clean_and_refuse_invalid_markdown(
    tmp_path: Path,
) -> None:
    copy_locked_markdownlint_runtime(tmp_path)
    lint_config = tmp_path / ".markdownlint-cli2.jsonc"
    lint_settings = b'{"config":{"MD013":false}}\n'
    lint_config.write_bytes(lint_settings)
    installed = run_basicly_consumer(tmp_path, "install")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert not (tmp_path / "package.json").exists()
    assert lint_config.read_bytes() == lint_settings
    hook = tmp_path / ".basicly/core/hooks/markdownlint.py"
    readme = tmp_path / "README.md"
    readme.write_text("# Consumer\n\nClean Markdown.\n", encoding="utf-8")
    clean = subprocess.run(
        [sys.executable, str(hook), "README.md"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert clean.returncode == 0, clean.stdout + clean.stderr
    assert "markdownlint-cli2" in clean.stdout
    assert "0 error(s)" in clean.stdout
    readme.write_text("# Consumer\n\nBad trailing spaces.   \n", encoding="utf-8")
    invalid = subprocess.run(
        [sys.executable, str(hook), "README.md"],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        check=False,
    )
    assert invalid.returncode != 0
    assert "MD009" in invalid.stdout + invalid.stderr


@pytest.mark.parametrize("dry_run", [False, True])
def test_install_refuses_an_unusable_node_without_writes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, dry_run: bool
) -> None:
    provision_markdownlint_double(tmp_path)
    tools = tmp_path / "tools"
    tools.mkdir()
    node = tools / ("node.cmd" if sys.platform == "win32" else "node")
    node.write_text(
        "@exit /b 7\n" if sys.platform == "win32" else f"#!{sys.executable}\nraise SystemExit(7)\n",
        encoding="utf-8",
    )
    node.chmod(0o755)
    monkeypatch.setenv("PATH", str(tools) + os.pathsep + os.environ["PATH"])
    before = _files(tmp_path)

    result = run_basicly_consumer(tmp_path, "install", *(["--dry-run"] if dry_run else []))

    assert result.returncode != 0
    assert "nvm install --lts" in result.stderr
    assert _files(tmp_path) == before


def test_enabled_dry_run_accepts_tools_without_writing(tmp_path: Path) -> None:
    provision_markdownlint_double(tmp_path)
    before = _files(tmp_path)

    result = run_basicly_consumer(tmp_path, "install", "--dry-run")

    assert result.returncode == 0, result.stdout + result.stderr
    assert "opted out" not in result.stdout
    assert _files(tmp_path) == before

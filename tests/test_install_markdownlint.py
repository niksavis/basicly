from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

from tests.markdownlint_runtime_fixture import (
    REPO_ROOT,
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


def _claim_install_record(consumer: Path, environment: dict[str, str]) -> str:
    prepared = subprocess.run(
        [
            sys.executable,
            ".basicly/core/kit/tracker/cli.py",
            "config",
            ".basicly/ledger",
            "set",
            "prefix",
            "myrepo",
        ],
        cwd=consumer,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert prepared.returncode == 0, prepared.stdout + prepared.stderr
    criterion = "When README.md is read, its heading shall name the scratch repository."
    created = run_basicly_consumer(
        consumer,
        "tracker",
        "write",
        "--",
        "create",
        "Install the harness",
        "-t",
        "task",
        "-d",
        "## Trigger\n\nWhen readers open this repository, I want the harness installed, "
        "so I can verify Markdown.\n\n## Scope\n\n- `README.md`\n- `NOTES.md`\n",
        "--acceptance",
        "- " + criterion,
        "--requirements",
        "Keep Markdown rules enabled.",
    )
    assert created.returncode == 0, created.stdout + created.stderr
    match = re.search(r"created: (myrepo-[a-z0-9]+)", created.stdout)
    assert match is not None
    issue = match.group(1)
    review = {
        "checks": [
            {
                "criterion": criterion,
                "command": ["grep", "-c", "Scratch repo", "README.md"],
                "expected": "1",
            }
        ]
    }
    reviewed = subprocess.run(
        [
            sys.executable,
            ".basicly/core/kit/tracker/cli.py",
            "review",
            ".basicly/ledger",
            issue,
            "--evidence",
            json.dumps(review),
        ],
        cwd=consumer,
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert reviewed.returncode == 0, reviewed.stdout + reviewed.stderr
    claimed = run_basicly_consumer(
        consumer, "tracker", "write", "--", "update", issue, "--status", "in_progress"
    )
    assert claimed.returncode == 0, claimed.stdout + claimed.stderr
    return issue


@pytest.mark.fresh_runtime
def test_documented_markdown_inputs_pass_real_commit_and_refuse_bad_consumer_markdown(
    tmp_path: Path,
) -> None:
    tutorial = (REPO_ROOT / "docs/tutorial/first-loop.md").read_text(encoding="utf-8")
    configurations = [
        json.loads(block)
        for block in re.findall(r"```json\n(.*?)\n```", tutorial, re.DOTALL)
        if '"globs"' in block
    ]
    assert configurations == [{"globs": ["README.md", "NOTES.md"]}]
    consumer = tmp_path / "consumer"
    consumer.mkdir()
    environment = {
        **os.environ,
        "PYTHONPATH": str(REPO_ROOT / "src"),
        "PATH": str(Path(sys.executable).parent) + os.pathsep + os.environ["PATH"],
    }
    entry = Path(sys.executable).parent / ("basicly.exe" if sys.platform == "win32" else "basicly")
    assert entry.is_file(), "the current project environment must provide its real basicly CLI"

    def git(*arguments: str) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            ["git", *arguments],
            cwd=consumer,
            env=environment,
            capture_output=True,
            text=True,
            check=False,
        )

    assert git("init", "-q", "-b", "main").returncode == 0
    assert git("config", "user.name", "Fixture Operator").returncode == 0
    assert git("config", "user.email", "fixture@example.invalid").returncode == 0
    (consumer / "README.md").write_text("# Scratch repo\n", encoding="utf-8")
    assert git("add", "README.md").returncode == 0
    seeded = git("commit", "-m", "chore: initialize scratch repository")
    assert seeded.returncode == 0, seeded.stdout + seeded.stderr
    (consumer / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    copy_locked_markdownlint_runtime(consumer)
    configuration = consumer / ".markdownlint-cli2.jsonc"
    owner_bytes = (json.dumps(configurations[0], indent=2) + "\n").encode()
    configuration.write_bytes(owner_bytes)

    installed = run_basicly_consumer(consumer, "install")
    assert installed.returncode == 0, installed.stdout + installed.stderr
    assert configuration.read_bytes() == owner_bytes

    issue = _claim_install_record(consumer, environment)
    assert git("add", "-A").returncode == 0
    committed = git("commit", "-m", f"chore: install basicly ({issue})")
    initial_output = committed.stdout + committed.stderr

    assert committed.returncode == 0, initial_output
    assert "Linting: 1 file(s)" in initial_output
    assert "Summary: 0 error(s)" in initial_output
    assert "catalog lint: OK" in initial_output
    assert configuration.read_bytes() == owner_bytes
    before = git("rev-parse", "HEAD").stdout
    (consumer / "NOTES.md").write_text("# Notes\n\nBad trailing spaces.   \n", encoding="utf-8")
    assert git("add", "NOTES.md").returncode == 0

    refused = git("commit", "-m", f"docs: add notes ({issue})")

    assert refused.returncode != 0
    assert "Linting: 2 file(s)" in refused.stdout + refused.stderr
    assert "MD009" in refused.stdout + refused.stderr
    assert git("rev-parse", "HEAD").stdout == before
    assert configuration.read_bytes() == owner_bytes

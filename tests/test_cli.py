"""Integration tests for the CLI."""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent


@pytest.fixture
def work_repo(tmp_path: Path) -> Path:
    """Return an isolated copy of the repo so tests never mutate real repo state."""
    work = tmp_path / "repo"
    shutil.copytree(REPO_ROOT, work, ignore=shutil.ignore_patterns(".git", ".venv"))
    return work


def run_basicly(cwd: Path, *args: str) -> subprocess.CompletedProcess[str]:
    """Run the basicly CLI with the given arguments in the given working directory."""
    env = {"PYTHONPATH": str(cwd / "src")}
    return subprocess.run(
        [sys.executable, "-m", "basicly.cli", *args],
        cwd=cwd,
        env=env,
        capture_output=True,
        text=True,
        check=False,
    )


def test_cli_build_idempotent(work_repo: Path) -> None:
    """Two build runs with no source changes should produce no diff."""
    result1 = run_basicly(work_repo, "build")
    assert result1.returncode == 0
    result2 = run_basicly(work_repo, "build")
    assert result2.returncode == 0
    assert "No files changed" in result2.stdout


def test_cli_check_passes_after_build(work_repo: Path) -> None:
    """Check should pass immediately after a build."""
    run_basicly(work_repo, "build")
    result = run_basicly(work_repo, "check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_cli_check_fails_after_manual_edit(work_repo: Path) -> None:
    """Check should fail after a generated file is edited manually."""
    run_basicly(work_repo, "build")
    agents = work_repo / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + "\n", encoding="utf-8")
    result = run_basicly(work_repo, "check")
    assert result.returncode == 1
    assert "Stale generated files detected" in result.stderr


def test_cli_build_target_only(work_repo: Path) -> None:
    """Build --target should only touch that target's outputs but preserve the manifest."""
    run_basicly(work_repo, "build")
    result = run_basicly(work_repo, "build", "--target", "claude")
    assert result.returncode == 0
    assert "copilot-instructions.md" not in result.stdout
    # Manifest must still list outputs from other targets so check passes.
    result_check = run_basicly(work_repo, "check")
    assert result_check.returncode == 0


def test_cli_unknown_target(work_repo: Path) -> None:
    """Build --target with an unknown target should fail cleanly."""
    result = run_basicly(work_repo, "build", "--target", "unknown")
    assert result.returncode == 1
    assert "Unknown target" in result.stderr


def test_cli_update_migrates_legacy_fragments(work_repo: Path) -> None:
    """Update migrates legacy .basicly/fragments into core and overlay roots."""
    legacy_core = work_repo / ".basicly" / "fragments" / "project"
    legacy_core.mkdir(parents=True, exist_ok=True)
    legacy_overlay = work_repo / ".basicly" / "fragments" / "user"
    legacy_overlay.mkdir(parents=True, exist_ok=True)

    legacy_core_file = legacy_core / "legacy-core.fragment.md"
    legacy_core_file.write_text(
        "---\n"
        "id: legacy-core\n"
        "description: legacy core\n"
        "category: project\n"
        "applies_to: [all]\n"
        "---\n\n"
        "legacy core\n",
        encoding="utf-8",
    )
    legacy_user_file = legacy_overlay / "legacy-user.fragment.md"
    legacy_user_file.write_text(
        "---\n"
        "id: legacy-user\n"
        "description: legacy user\n"
        "category: project\n"
        "applies_to: [all]\n"
        "---\n\n"
        "legacy user\n",
        encoding="utf-8",
    )

    result = run_basicly(work_repo, "update")

    assert result.returncode == 0
    assert (
        work_repo / ".basicly" / "core" / "fragments" / "project" / "legacy-core.fragment.md"
    ).exists()
    assert (
        work_repo / ".basicly-local" / "fragments" / "user" / "legacy-user.fragment.md"
    ).exists()


def test_cli_skills_build_idempotent(work_repo: Path) -> None:
    """Two skills-build runs with no source changes should produce no diff."""
    result1 = run_basicly(work_repo, "skills-build")
    assert result1.returncode == 0
    result2 = run_basicly(work_repo, "skills-build")
    assert result2.returncode == 0
    assert "No skill files changed" in result2.stdout


def test_cli_skills_check_passes_after_build(work_repo: Path) -> None:
    """skills-check should pass immediately after a skills-build run."""
    run_basicly(work_repo, "skills-build")
    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 0
    assert "up to date" in result.stdout


def test_cli_skills_check_fails_after_manual_edit(work_repo: Path) -> None:
    """skills-check should fail after an edited projected skill file."""
    run_basicly(work_repo, "skills-build")

    projected_skill = work_repo / ".claude" / "skills" / "tool-ripgrep" / "SKILL.md"
    projected_skill.write_text(
        projected_skill.read_text(encoding="utf-8") + "\n",
        encoding="utf-8",
    )

    result = run_basicly(work_repo, "skills-check")
    assert result.returncode == 1
    assert "Stale skill projection detected" in result.stderr

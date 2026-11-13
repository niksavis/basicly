from __future__ import annotations

import json
import os
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest


def _run(repo: Path, *argv: str) -> subprocess.CompletedProcess[str]:
    result = subprocess.run(  # nosec B603
        list(argv),
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo / "src")},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    return result


def _build(repo: Path, *args: str) -> None:
    _run(repo, sys.executable, "-m", "basicly.cli", "build", *args)


@pytest.mark.parametrize("target", [(), ("--target", "claude")])
def test_repeated_builds_leave_all_tracked_files_clean(
    work_repo: Path, target: tuple[str, ...]
) -> None:
    _build(work_repo)
    _run(work_repo, "git", "init", "-q")
    _run(work_repo, "git", "add", "--all")
    _run(
        work_repo,
        "git",
        "-c",
        "user.name=fixture",
        "-c",
        "user.email=fixture@example.invalid",
        "-c",
        "commit.gpgsign=false",
        "commit",
        "-qm",
        "fixture baseline",
    )
    assert not _run(work_repo, "git", "status", "--porcelain").stdout

    for _ in range(2):
        _build(work_repo, *target)
        status = _run(work_repo, "git", "status", "--porcelain").stdout
        assert not status, f"build changed tracked state:\n{status}"

    fragment = work_repo / ".basicly/core/fragments/design/knowledge-priming.fragment.yaml"
    fragment.write_text(
        fragment.read_text(encoding="utf-8") + "  - Show the fixture control sentence.\n",
        encoding="utf-8",
    )
    _build(work_repo, *target)
    status = _run(work_repo, "git", "status", "--porcelain").stdout
    assert " M .basicly/generated-manifest.json" in status
    assert " M .claude/CLAUDE.md" in status
    assert "Show the fixture control sentence." in (work_repo / ".claude/CLAUDE.md").read_text(
        encoding="utf-8"
    )


def test_build_and_check_accept_a_manifest_with_the_legacy_timestamp(work_repo: Path) -> None:
    _build(work_repo)
    path = work_repo / ".basicly/generated-manifest.json"
    manifest = json.loads(path.read_text(encoding="utf-8"))
    manifest["generated_at"] = "2026-01-01T00:00:00+00:00"
    path.write_text(json.dumps(manifest, indent=2) + "\n", encoding="utf-8")

    _run(work_repo, sys.executable, "-m", "basicly.cli", "check")
    _build(work_repo)

    rebuilt = json.loads(path.read_text(encoding="utf-8"))
    assert rebuilt["outputs"] == manifest["outputs"]
    assert "generated_at" not in rebuilt

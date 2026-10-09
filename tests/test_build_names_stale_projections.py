from __future__ import annotations

import argparse
import os
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import cli

if TYPE_CHECKING:
    import pytest

STYLE_SOURCE = ".basicly/core/output-styles/tired-engineer/style.yaml"


def _build(repo: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, "-m", "basicly.cli", "build"],
        cwd=repo,
        env={**os.environ, "PYTHONPATH": str(repo / "src")},
        capture_output=True,
        text=True,
        check=False,
    )


def test_build_names_a_stale_style_and_its_build_command(work_repo: Path) -> None:
    source = work_repo / STYLE_SOURCE
    source.write_text(
        source.read_text(encoding="utf-8").replace("a tired engineer.", "a reader."),
        encoding="utf-8",
    )

    result = _build(work_repo)

    assert result.returncode == 0, result.stderr
    output = result.stdout + result.stderr
    assert "Output styles are stale. Run `basicly styles-build`." in output
    assert "Skills are stale" not in output


def test_build_prints_nothing_more_when_every_projection_is_in_sync(work_repo: Path) -> None:
    result = _build(work_repo)

    assert result.returncode == 0, result.stderr
    assert "are stale" not in result.stdout + result.stderr


def test_hooks_check_and_build_read_the_same_mismatch_function(
    work_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(work_repo)
    monkeypatch.setattr(
        cli, "_hook_mismatches", lambda repo_root: [(repo_root / "x", "planted mismatch")]
    )

    assert cli.cmd_hooks_check(argparse.Namespace()) == 1
    assert ("Hooks", "hooks-build") in cli._stale_projections(work_repo)

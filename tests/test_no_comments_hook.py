from __future__ import annotations

import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "no-comments.py"


def _run(paths: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT), *paths],
        capture_output=True,
        text=True,
        check=False,
        cwd=REPO_ROOT,
    )


def test_a_prose_comment_is_refused_and_the_fix_is_named(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("# what this does\nx = 1\n", encoding="utf-8")

    result = _run([str(path)])

    assert result.returncode == 1
    assert "a.py:1" in result.stdout
    assert "the code is the source of truth" in result.stderr
    assert "cli.py fix" in result.stderr


def test_a_clean_file_says_nothing(tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text("x = 1\n", encoding="utf-8")

    result = _run([str(path)])

    assert result.returncode == 0
    assert result.stdout == ""


@pytest.mark.parametrize(
    "line",
    ["# noqa: F401", "# nosec B603", "# type: ignore[arg-type]", "#!/usr/bin/env python3"],
)
def test_a_directive_never_blocks_a_commit(line: str, tmp_path: Path) -> None:
    path = tmp_path / "a.py"
    path.write_text(f"{line}\nimport os\n\nprint(os)\n", encoding="utf-8")

    assert _run([str(path)]).returncode == 0


def test_no_paths_is_not_a_refusal() -> None:
    assert _run([]).returncode == 0


def test_the_live_tree_passes_its_own_gate() -> None:
    listed = subprocess.run(  # nosec B603 B607
        ["git", "ls-files", "*.py"],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.split()
    assert len(listed) > 500, "the positive control: the tree should hold hundreds of modules"

    result = _run(listed)

    assert result.returncode == 0, result.stdout

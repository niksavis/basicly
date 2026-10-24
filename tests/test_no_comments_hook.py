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


def _git(repo: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=repo, check=True, capture_output=True)  # nosec B603 B607


def _repo_with_commented_module(tmp_path: Path) -> Path:
    _git(tmp_path, "init", "-q")
    _git(tmp_path, "config", "user.email", "ada@personal.example")
    _git(tmp_path, "config", "user.name", "Ada Lovelace")
    (tmp_path / "a.py").write_text('"""legacy module docstring"""\n# old\nx = 1\n', "utf-8")
    _git(tmp_path, "add", "a.py")
    _git(tmp_path, "commit", "-q", "-m", "seed")
    return tmp_path


def _run_in(repo: Path, paths: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT), *paths],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=repo,
    )


def test_comments_a_tracked_file_already_held_do_not_block_an_unrelated_change(
    tmp_path: Path,
) -> None:
    repo = _repo_with_commented_module(tmp_path)
    (repo / "a.py").write_text('"""legacy module docstring"""\n# old\nx = 1\ny = 2\n', "utf-8")
    _git(repo, "add", "a.py")

    result = _run_in(repo, ["a.py"])

    assert result.returncode == 0, result.stdout + result.stderr


def test_a_comment_added_to_a_tracked_file_is_refused(tmp_path: Path) -> None:
    repo = _repo_with_commented_module(tmp_path)
    (repo / "a.py").write_text(
        '"""legacy module docstring"""\n# old\nx = 1\n# new prose\ny = 2\n', "utf-8"
    )
    _git(repo, "add", "a.py")

    result = _run_in(repo, ["a.py"])

    assert result.returncode == 1
    assert "a.py:4" in result.stdout
    assert "a.py:2" not in result.stdout


def test_a_newly_added_file_is_checked_whole(tmp_path: Path) -> None:
    repo = _repo_with_commented_module(tmp_path)
    (repo / "b.py").write_text("# fresh prose\nz = 3\n", "utf-8")
    _git(repo, "add", "b.py")

    result = _run_in(repo, ["b.py"])

    assert result.returncode == 1
    assert "b.py:1" in result.stdout

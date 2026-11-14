from __future__ import annotations

import json
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / ".basicly" / "core" / "hooks" / "no-comments.py"


def _run(paths: list[str]) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT), "--paths-json-stdin"],
        input=json.dumps(paths),
        capture_output=True,
        text=True,
        encoding="utf-8",
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
    listed = (
        subprocess
        .run(  # nosec B603 B607
            ["git", "ls-files", "-z", "--", "*.py"],
            cwd=REPO_ROOT,
            capture_output=True,
            text=True,
            encoding="utf-8",
            check=True,
        )
        .stdout.rstrip("\0")
        .split("\0")
    )
    assert len(listed) > 500, "the positive control: the tree should hold hundreds of modules"

    result = _run(listed)

    assert result.returncode == 0, result.stdout


def test_large_path_input_still_checks_the_last_file(tmp_path: Path) -> None:
    clean = tmp_path / "clean.py"
    clean.write_text("value = 1\n")
    last = tmp_path / "last.py"
    last.write_text("# The final file must still be checked.\nvalue = 1\n")
    result = _run([str(clean)] * 2000 + [str(last)])
    assert result.returncode == 1
    assert "last.py:1" in result.stdout


def test_malformed_path_input_refuses_instead_of_checking_an_empty_set() -> None:
    result = subprocess.run(
        [sys.executable, str(SCRIPT), "--paths-json-stdin"],
        input='["valid.py", null]',
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 1
    assert "requires a JSON list" in result.stderr


def test_direct_path_arguments_still_refuse_prose(tmp_path: Path) -> None:
    path = tmp_path / "direct.py"
    path.write_text("# This prose must be refused.\nvalue = 1\n", encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(SCRIPT), str(path)],
        cwd=REPO_ROOT,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == 1
    assert "direct.py:1" in result.stdout

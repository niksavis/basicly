from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[2] / ".basicly/core/hooks/hook-contracts.py"


@pytest.mark.parametrize(
    ("source", "expected", "message"),
    [
        ("import subprocess\nsubprocess.run(['git'], text=True)\n", 1, "encoding"),
        ("import subprocess\nsubprocess.run(['git'], universal_newlines=True)\n", 1, "encoding"),
        ("import subprocess\nsubprocess.run(['git'], text=True, encoding=None)\n", 1, "encoding"),
        ("def broken(:\n", 1, "syntax"),
        ("import subprocess\nsubprocess.run(['git'], text=True, encoding='utf-8')\n", 0, "passed"),
        ("import subprocess\nsubprocess.run(['git'], capture_output=True)\n", 0, "passed"),
        ("try:\n    pass\nexcept ValueError, TypeError:\n    pass\n", 0, "passed"),
    ],
)
def test_standalone_consumer_hook_contracts(
    tmp_path: Path, source: str, expected: int, message: str
) -> None:
    hooks = tmp_path / ".basicly/core/hooks"
    hooks.mkdir(parents=True)
    guard = hooks / SOURCE.name
    shutil.copyfile(SOURCE, guard)
    (hooks / "fixture.py").write_text(source, encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(guard)],
        cwd=tmp_path,
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=False,
    )
    assert result.returncode == expected, result.stdout + result.stderr
    assert message in result.stdout + result.stderr


def test_contract_validation_does_not_execute_hook_code(tmp_path: Path) -> None:
    hooks = tmp_path / "hooks"
    hooks.mkdir()
    guard = hooks / SOURCE.name
    shutil.copyfile(SOURCE, guard)
    (hooks / "fixture.py").write_text("raise RuntimeError('must not execute')\n", encoding="utf-8")
    result = subprocess.run([sys.executable, str(guard)], cwd=tmp_path, check=False)
    assert result.returncode == 0

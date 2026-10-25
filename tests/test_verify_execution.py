from __future__ import annotations

import sys
from pathlib import Path

import pytest

from basicly import verify_execution


@pytest.mark.parametrize("capture", [True, False])
def test_execution_preserves_output_exit_code_and_cwd(
    tmp_path: Path, capture: bool, capsys: pytest.CaptureFixture[str]
) -> None:
    command = [
        sys.executable,
        "-c",
        "from pathlib import Path; print(Path.cwd().name); raise SystemExit(7)",
    ]
    result = verify_execution.spawn(command, tmp_path, capture=capture)
    assert result.returncode == 7
    assert result.stdout == tmp_path.name + "\n"
    if not capture:
        assert capsys.readouterr().out == result.stdout

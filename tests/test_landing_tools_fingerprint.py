from __future__ import annotations

import os
import sys
from pathlib import Path

import pytest

from basicly import pytest_inputs


def _launcher(folder: Path, shebang_note: str) -> str:
    folder.mkdir(parents=True)
    tool = folder / "pytest"
    tool.write_text(f"#!/bin/sh\n# {shebang_note}\necho 'pytest 9.0.0'\n", encoding="utf-8")
    tool.chmod(0o755)
    return str(tool)


@pytest.mark.skipif(sys.platform == "win32", reason="the stand-in launcher is a POSIX shell script")
def test_two_launchers_of_one_version_give_the_same_tools_entry(tmp_path: Path) -> None:
    environment = dict(os.environ)
    main = _launcher(tmp_path / "main" / "bin", "main venv")
    lane = _launcher(tmp_path / "lane" / "bin", "lane venv with another shebang")

    assert Path(main).read_bytes() != Path(lane).read_bytes()
    shown = {pytest_inputs._tool_version(found, tmp_path, environment) for found in (main, lane)}
    assert shown == {"pytest 9.0.0"}

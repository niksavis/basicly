from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks"


@pytest.mark.parametrize("script", sorted(HOOKS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_hook_script_compiles_at_the_supported_floor(script: Path, tmp_path: Path) -> None:
    py_compile.compile(str(script), cfile=str(tmp_path / "out.pyc"), doraise=True)

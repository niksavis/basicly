"""Every catalog hook script must stay within the supported Python floor.

The scripts execute under the *consumer's* interpreter — not the engine's —
so nothing else pins their syntax. Python 3.14+ is the documented consumer
requirement, and CI runs exactly that floor: compiling every script here
proves they parse on the oldest interpreter we support, and fails the suite
if a script ever adopts syntax newer than the floor.
"""

from __future__ import annotations

import py_compile
from pathlib import Path

import pytest

HOOKS_DIR = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks"


@pytest.mark.parametrize("script", sorted(HOOKS_DIR.glob("*.py")), ids=lambda p: p.name)
def test_hook_script_compiles_at_the_supported_floor(script: Path, tmp_path: Path) -> None:
    """Each shipped hook script parses under the running (floor) interpreter."""
    py_compile.compile(str(script), cfile=str(tmp_path / "out.pyc"), doraise=True)

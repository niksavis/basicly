from __future__ import annotations

import ast
import tomllib
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit"
MANIFEST = REPO_ROOT / "packages" / "basicly-tracker" / "pyproject.toml"


def _floor() -> tuple[int, int]:
    manifest = tomllib.loads(MANIFEST.read_text(encoding="utf-8"))
    major, minor = manifest["project"]["requires-python"].removeprefix(">=").split(".")[:2]
    return int(major), int(minor)


def _refused(path: Path, floor: tuple[int, int]) -> str | None:
    try:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path), feature_version=floor)
    except SyntaxError as error:
        return f"{path}:{error.lineno}: {error.msg}"
    return None


def test_a_paren_free_except_is_refused_at_the_floor(tmp_path: Path) -> None:
    planted = tmp_path / "planted.py"
    planted.write_text("try:\n    pass\nexcept A, B:\n    pass\n", encoding="utf-8")

    refused = _refused(planted, _floor())

    assert refused is not None
    assert "planted.py" in refused


@pytest.mark.parametrize("path", sorted(KIT.rglob("*.py")), ids=lambda p: str(p.relative_to(KIT)))
def test_every_kit_file_parses_at_the_standalone_python_floor(path: Path) -> None:
    assert _refused(path, _floor()) is None

from __future__ import annotations

import importlib
import sys
import tomllib
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / ".basicly" / "core" / "kit" / "orch"))

cli = importlib.import_module("orch_lessons.cli")


def test_the_orch_package_takes_the_basicly_version_from_one_source() -> None:
    project = tomllib.loads((ROOT / "packages/basicly-orch/pyproject.toml").read_text("utf-8"))

    assert "version" not in project["project"]
    assert project["project"]["dynamic"] == ["version"]
    assert project["tool"]["hatch"]["version"]["path"] == "../../src/basicly/__init__.py"


def test_orch_help_lists_brief_and_accept() -> None:
    shown = cli.parser().format_help()

    assert "brief" in shown
    assert "accept" in shown

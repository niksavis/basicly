from __future__ import annotations

import importlib
import importlib.metadata
import sys
import tomllib
from pathlib import Path

import pytest

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


def test_orch_version_prints_the_installed_package_version(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr(
        importlib.metadata, "version", lambda name: {"basicly-orch": "0.49.1"}[name]
    )

    with pytest.raises(SystemExit) as stopped:
        cli.parser().parse_args(["--version"])

    assert stopped.value.code == 0
    assert capsys.readouterr().out.strip() == "orch 0.49.1"

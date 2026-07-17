"""Tests for the catalog-lint hook's CLI resolution ladder (basicly-7o8)."""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path

import pytest


def _load_hook():
    """Load the catalog-lint hook module from its script path."""
    script_path = (
        Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "catalog-lint.py"
    )
    spec = importlib.util.spec_from_file_location("catalog_lint_hook", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_prefers_basicly_on_path(monkeypatch: pytest.MonkeyPatch) -> None:
    """An installed basicly executable wins."""
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/basicly" if name == "basicly" else None
    )
    assert module._cli_command() == ["/usr/bin/basicly", "catalog", "lint"]


def test_falls_back_to_importable_module(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without the executable, an importable basicly package is used."""
    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    # The test venv has basicly importable, so the real find_spec resolves it.
    assert module._cli_command() == [sys.executable, "-m", "basicly.cli", "catalog", "lint"]


def test_falls_back_to_uvx(monkeypatch: pytest.MonkeyPatch) -> None:
    """A consumer without the package resolves through uvx."""
    module = _load_hook()
    monkeypatch.setattr(
        module.shutil, "which", lambda name: "/usr/bin/uvx" if name == "uvx" else None
    )
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)
    assert module._cli_command() == [
        "/usr/bin/uvx",
        "--from",
        module.DIST_SOURCE,
        "basicly",
        "catalog",
        "lint",
    ]


def test_advisory_skip_when_no_channel(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """No executable, no package, no uvx: warn and pass — never block commits.

    Regression (basicly-7o8): the hook hard-failed every consumer commit with
    ModuleNotFoundError (observed live in the terminal repo).
    """
    module = _load_hook()
    monkeypatch.setattr(module.shutil, "which", lambda _name: None)
    monkeypatch.setattr(module.importlib.util, "find_spec", lambda _name: None)

    assert module.main() == 0
    err = capsys.readouterr().err
    assert "catalog-lint skipped" in err
    assert "basicly-gates.yml" in err

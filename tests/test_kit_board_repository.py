from __future__ import annotations

import importlib.util
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import Any

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "board"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = _load(KIT_DIR / "server.py", "basicly_tracker_kit_server")
cli = server.tracker_cli()


def _ledger(root: Path) -> Path:
    ledger = root / ".basicly" / "ledger"
    argv = ["create", str(ledger), "--prefix", "demo", "--title", "seed"]
    assert cli.main([*argv, "--description", "## Context\n\nSeed.", "--acceptance", "- a"]) == 0
    return ledger


def test_the_api_names_the_repository_that_holds_the_ledger(tmp_path: Path) -> None:
    repo = tmp_path / "acme-shop"
    subprocess.run(["git", "init", "-q", str(repo)], check=True)  # nosec B603 B607

    index = server.api_index(_ledger(repo))

    assert index["repository"] == "acme-shop"


def test_a_ledger_outside_a_checkout_names_no_repository(tmp_path: Path) -> None:
    index = server.api_index(_ledger(tmp_path / "loose"))

    assert index["repository"] == ""


def test_the_page_titles_itself_with_the_repository() -> None:
    page = (KIT_DIR / "web" / "index.html").read_text(encoding="utf-8")

    assert "index.repository" in page
    assert "document.title" in page

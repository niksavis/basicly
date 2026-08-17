"""Tests for the corpus-drift gate (basicly-b9ef).

Driven against fixture exports written into `tmp_path`, never against this repo's own
tracker: a gate asserted on live bead text becomes a report on whatever the tracker holds
today, and any lane editing a bead turns the suite red. The one real-tree run asserts only
that the entry point runs and identifies itself, which is what a hardcoded ``REPO_ROOT``
can be held to.

The ratchet is pinned in both directions for the reason `module-size` states: a recorded
count that could rise is a licence, and one that could fall unbanked licenses regrowth
back to the higher number.

The wiring test is the one that matters most here — an instrument built and never
connected is this repo's named defect class, and `basicly.toml` is where this one becomes
a gate rather than a script.
"""

from __future__ import annotations

import importlib.util
import sys
import tomllib
from pathlib import Path
from types import ModuleType

from tests import flipped_tracker

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_corpus_drift.py"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_corpus_drift")


def _export(repo_root: Path, records: list[dict]) -> None:
    """Seed *records* in the committed ledger the gate reads."""
    flipped_tracker.seed_records(repo_root, records)


def test_the_gate_is_wired_as_a_verify_check() -> None:
    """An instrument nothing runs is the defect class this repo keeps paying for."""
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]
    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["corpus-drift"]
    assert wired[0]["modes"] == ["fast", "full"]

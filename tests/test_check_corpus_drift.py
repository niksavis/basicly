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
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_corpus_drift")


def _export(repo_root: Path, records: list[dict]) -> None:
    flipped_tracker.seed_records(repo_root, records)


def test_the_gate_is_wired_as_a_verify_check() -> None:
    config = tomllib.loads((REPO_ROOT / "basicly.toml").read_text(encoding="utf-8"))
    checks = config["verify"]["checks"]
    wired = [check for check in checks if SCRIPT.name in " ".join(check["command"])]
    assert [check["name"] for check in wired] == ["corpus-drift"]
    assert wired[0]["modes"] == ["fast", "full"]

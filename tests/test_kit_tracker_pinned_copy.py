from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess  # nosec B404
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    import pytest

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
PACKAGE = REPO_ROOT / "packages" / "basicly-tracker" / "basicly_tracker" / "__init__.py"
PINNED = "0.0.9"


def _package(monkeypatch: pytest.MonkeyPatch) -> Any:
    spec = importlib.util.spec_from_file_location("basicly_tracker_pinned_test", PACKAGE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["basicly_tracker_pinned_test"] = module
    spec.loader.exec_module(module)
    monkeypatch.setattr(module, "KIT", module.KIT._replace(directory=KIT_DIR))
    return module


def _tracker(cli: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(cli), *args], capture_output=True, text=True, check=False
    )


def _pinned_repo(tmp_path: Path, *, vendored: bool) -> Path:
    ledger = tmp_path / "repo" / ".basicly" / "ledger"
    made = _tracker(KIT_DIR / "cli.py", "create", str(ledger), "--prefix", "demo", "--title", "x")
    assert made.returncode == 0, made.stdout
    (ledger / ".kit-version").write_text(f"{PINNED}\n", encoding="utf-8")
    if vendored:
        copy = tmp_path / "repo" / ".basicly" / "kit" / "tracker"
        shutil.copytree(KIT_DIR, copy, ignore=shutil.ignore_patterns("__pycache__"))
        pin = copy / "pin.py"
        text = pin.read_text(encoding="utf-8")
        current = text.split('KIT_VERSION = "', 1)[1].split('"', 1)[0]
        pin.write_text(text.replace(f'"{current}"', f'"{PINNED}"', 1), encoding="utf-8")
    return ledger


def test_a_tracker_of_another_version_runs_the_pinned_copy_beside_the_ledger(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    ledger = _pinned_repo(tmp_path, vendored=True)
    capfd.readouterr()

    code = _package(monkeypatch).main(["stats", str(ledger)])

    out = capfd.readouterr().out
    assert code == 0, out
    assert json.loads(out)["schema"].startswith("basicly.tracker.stats")


def test_without_a_pinned_copy_the_mismatch_is_refused_with_the_commands(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capfd: pytest.CaptureFixture[str]
) -> None:
    ledger = _pinned_repo(tmp_path, vendored=False)
    capfd.readouterr()

    code = _package(monkeypatch).main(["stats", str(ledger)])

    assert code != 0
    assert f"this ledger pins tracker {PINNED}" in json.loads(capfd.readouterr().out)["refused"]

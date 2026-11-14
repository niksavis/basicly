from __future__ import annotations

import importlib
import json
import subprocess
from pathlib import Path
from typing import Any

import pytest


def test_actual_engine_write_reports_and_events_match_the_released_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = Path(__file__).resolve().parents[1] / ".scripts"
    monkeypatch.syspath_prepend(str(scripts))
    proof = importlib.import_module("tracker_engine_compat")

    report = proof.compare("v0.45.0", tmp_path / "engine-proof")

    assert report["result"] == "pass"
    assert report["forms"] == 9
    assert report["reads"] == 7


def _proof(monkeypatch: pytest.MonkeyPatch) -> Any:
    scripts = Path(__file__).resolve().parents[1] / ".scripts"
    monkeypatch.syspath_prepend(str(scripts))
    return importlib.import_module("tracker_engine_compat")


def test_a_real_engine_version_change_keeps_its_diagnostic_and_all_legacy_behavior(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof = _proof(monkeypatch)
    source = tmp_path / "versioned-engine"
    proof._engine("HEAD", source)
    version = source / "src/basicly/__init__.py"
    original = version.read_text(encoding="utf-8")
    assert original.count("__version__ = ") == 1
    version.write_text('__version__ = "0.0.0-proof"\n', encoding="utf-8")

    report = proof.compare("v0.45.0", tmp_path / "version-proof", candidate_source=source / "src")

    assert report["result"] == "pass"
    reports = json.loads((tmp_path / "version-proof/reports.json").read_text(encoding="utf-8"))
    assert reports["candidate"]["engine_version"] == "0.0.0-proof"
    assert reports["released"]["engine_version"] != "0.0.0-proof"


@pytest.mark.parametrize("change", ["version", "report", "event", "formatting"])
def test_the_comparison_refuses_wrong_diagnostics_and_every_other_byte_change(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, change: str
) -> None:
    proof = _proof(monkeypatch)
    original = proof.run

    def altered(argv: list[str], root: Path, env: dict[str, str]) -> Any:
        result = original(argv, root, env)
        if root.name != "candidate-repository" or result.returncode:
            return result
        payload = json.loads(result.stdout)
        if change == "event":
            payload["events"][0]["actor"] = "a changed event actor"
        elif change == "report":
            payload["calls"][0]["stdout"] += "changed"
        else:
            row = next(row for row in payload["calls"] if row["argv"] == ["describe", "--json"])
            if change == "version":
                row["stdout"] = row["stdout"].replace(payload["engine_version"], "wrong-version")
            else:
                row["stdout"] = row["stdout"].replace('"name":', '"name": ')
        return subprocess.CompletedProcess(argv, 0, json.dumps(payload), result.stderr)

    monkeypatch.setattr(proof, "run", altered)

    with pytest.raises(ValueError, match=r"actual engine version|reports or events differ"):
        proof.compare("v0.45.0", tmp_path / ("invalid-" + change))

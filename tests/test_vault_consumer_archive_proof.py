from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any

from tests.test_kit_ledger_freeze import KIT
from tests.test_kit_vault_share import bare

if TYPE_CHECKING:
    import pytest

__all__ = ["bare"]

SCRIPTS = Path(__file__).resolve().parents[1] / ".scripts"


def _proof(monkeypatch: pytest.MonkeyPatch) -> Any:
    monkeypatch.syspath_prepend(str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(
        "archive_consumer_proof", SCRIPTS / "check_vault_lifecycle.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, spec.name, module)
    spec.loader.exec_module(module)
    return module


def test_the_local_write_proof_observes_the_guard_config_and_archived_history(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof = _proof(monkeypatch)
    archive = tmp_path / ".basicly/ledger-archive"
    archive.mkdir(parents=True)
    paths = [
        tmp_path / ".basicly/ledger",
        tmp_path / ".basicly-tracker.toml",
        archive / "events-0001.jsonl",
        archive / "pending-worker.jsonl",
    ]
    for path in paths:
        path.write_text("original", encoding="utf-8")
    before = proof.local_logs(tmp_path)
    assert len(before) == len(paths)

    for path in paths:
        path.write_text("changed", encoding="utf-8")
        assert proof.local_logs(tmp_path) != before
        path.write_text("original", encoding="utf-8")
        assert proof.local_logs(tmp_path) == before


def test_the_complete_consumer_proof_runs_after_real_import_and_freeze(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    proof = _proof(monkeypatch)
    state = tmp_path / "state"
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(state))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "frozen-consumer-proof")
    monkeypatch.setenv("BASICLY_HOLDER", "consumer-proof")
    monkeypatch.delenv("CI", raising=False)
    monkeypatch.delenv("BASICLY_TRACKER_EPHEMERAL", raising=False)
    control, workspace = tmp_path / "control", tmp_path / "workspace"
    proof.prepare(KIT, control, "proof")
    proof.prepare(KIT, workspace, "work")
    record = json.loads((workspace / proof.MARKER).read_text())["record"]
    invoke = proof.caller(KIT, workspace, [])
    invoke("comment", proof.LEDGER, record, "A longer pre-migration history")
    invoke(
        "review",
        proof.LEDGER,
        record,
        "--evidence",
        json.dumps({
            "checks": [
                {"criterion": proof.CRITERION, "command": proof.COMMAND, "expected": "saved"}
            ]
        }),
    )
    for repository, prefix in ((control, "proof"), (workspace, "work")):
        invoke = proof.caller(KIT, repository, [])
        invoke(
            "migrate",
            proof.LEDGER,
            "--to-vault",
            "--vault",
            str(bare),
            "--state",
            str(state),
            "--no-map-kept",
        )
        invoke(
            "migrate",
            proof.LEDGER,
            "--to-vault",
            "--freeze",
            "--vault",
            str(bare),
            "--state",
            str(state),
            "--prefix",
            prefix,
        )
    output = tmp_path / "result.json"

    proof.check(KIT, control, workspace, record, output)

    report = json.loads(output.read_text())
    assert report["result"] == "pass"
    assert report["local_ledger_writes"] == 0
    assert report["real_workspace_control"]["new_events"] == 2

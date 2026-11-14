from __future__ import annotations

import json
import runpy
import shutil
from pathlib import Path

import pytest

SOURCE = Path(__file__).resolve().parents[1] / ".scripts/vault_rehearsal_evidence.py"


def test_changed_evidence_runner_invalidates_retained_pass(tmp_path: Path) -> None:
    source = Path(__file__).resolve().parents[1] / ".scripts/vault_rehearsal_evidence.py"
    runner = tmp_path / ".scripts/vault_rehearsal_evidence.py"
    runner.parent.mkdir()
    shutil.copyfile(source, runner)
    helper = runner.with_name("rehearse_vault_migration.py")
    helper.write_text("", encoding="utf-8")
    cli = tmp_path / ".basicly/core/kit/tracker/cli.py"
    cli.parent.mkdir(parents=True)
    cli.write_text("", encoding="utf-8")
    module = runpy.run_path(str(runner))
    before = module["provenance"](tmp_path, helper)
    output = tmp_path / "reports"
    output.mkdir()
    (output / "part1.json").write_text(json.dumps({"result": "pass", "provenance": before}))
    runner.write_text(runner.read_text() + "\n")

    after = module["provenance"](tmp_path, helper)

    assert before["kit_sha256"] == after["kit_sha256"]
    assert before["helper_sha256"] != after["helper_sha256"]
    with pytest.raises(ValueError, match="source changed"):
        module["require_source"](output, after)


def test_a_freeze_success_report_requires_the_real_file_guard(tmp_path: Path) -> None:
    module = runpy.run_path(str(SOURCE))
    repository = tmp_path / "repository"
    ledger = repository / ".basicly/ledger"
    ledger.mkdir(parents=True)
    original = ledger / "events-0001.jsonl"
    original.write_text("original\n", encoding="utf-8")

    def reported_success(_argv: list[str], _cwd: Path) -> str:
        return json.dumps({"frozen": True})

    with pytest.raises(ValueError, match="did not install its file guard and archive"):
        module["freeze_consumer"](repository, "proof", tmp_path, tmp_path, reported_success)

    assert original.read_text() == "original\n"


def test_a_freeze_success_report_cannot_hide_changed_archive_bytes(tmp_path: Path) -> None:
    module = runpy.run_path(str(SOURCE))
    repository = tmp_path / "repository"
    ledger = repository / ".basicly/ledger"
    ledger.mkdir(parents=True)
    (ledger / "events-0001.jsonl").write_text("original\n", encoding="utf-8")
    archive = ledger.with_name("ledger-archive")

    def corrupt_archive(_argv: list[str], _cwd: Path) -> str:
        ledger.rename(archive)
        ledger.write_text("guard\n", encoding="utf-8")
        (archive / "events-0001.jsonl").write_text("changed\n", encoding="utf-8")
        return json.dumps({"frozen": True})

    with pytest.raises(ValueError, match="changed original ledger bytes"):
        module["freeze_consumer"](repository, "proof", tmp_path, tmp_path, corrupt_archive)

    assert (archive / "events-0001.jsonl").read_text() == "changed\n"

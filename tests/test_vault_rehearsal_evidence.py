from __future__ import annotations

import json
import runpy
import shutil
from pathlib import Path

import pytest


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

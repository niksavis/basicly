from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_kit_vault_cli_wiring import LEDGER, TRIGGER, _environment, _kit, _repo, _report
from tests.test_kit_vault_lifecycle import _run
from tests.test_kit_vault_share import bare

if TYPE_CHECKING:
    import pytest

__all__ = ["_environment", "bare"]


def test_a_vault_sheet_applies_and_the_record_becomes_ready(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _repo(tmp_path, bare)
    record = _run(
        root,
        "create",
        LEDGER,
        "--title",
        "A vault task",
        "--field",
        "issue_type=task",
        "--field",
        "size=small",
        "--description",
        TRIGGER,
    )["record"]
    sheet = tmp_path / "sheet.txt"
    _run(root, "refine-sheet", LEDGER, "--out", str(sheet))
    sheet.write_text(
        sheet.read_text(encoding="utf-8").replace(
            "acceptance:\n- \n", "acceptance:\n- It works.\n"
        ),
        encoding="utf-8",
    )

    done = _kit(root, "apply-sheet", LEDGER, str(sheet))

    assert done.returncode == 0, done.stdout + done.stderr
    assert _report(done)["applied"] == [record]
    ready = _run(root, "ready", LEDGER)
    assert record in json.dumps(ready)

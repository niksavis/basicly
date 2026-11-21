from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_orch_status import cli, workspace

if TYPE_CHECKING:
    import pytest


def test_state_set_stores_the_item_and_the_last_result_and_status_shows_them(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("COLUMNS", "200")
    base, repo = workspace(tmp_path)
    env = {"ORCH_SESSION_ID": "abc", "ORCH_MEMBER": "dev"}
    told = ["state", "set", "--item", "acme-a1b2", "--now", "lands lane A", "--state", "working"]

    assert cli.run([*told, "--last", "CI red on shard 3", "--failed"], repo, env) == 0
    assert cli.run(["status"], repo, env) == 0

    saved = json.loads((base / "sessions" / "abc.json").read_text(encoding="utf-8"))
    assert saved["item"] == "acme-a1b2"
    assert (saved["last"], saved["last_ok"]) == ("CI red on shard 3", False)
    assert saved["last_at"]
    row = capsys.readouterr().out.splitlines()[1]
    assert row.endswith("acme-a1b2 | lands lane A | last failed: CI red on shard 3")


def test_a_last_result_without_failed_is_ok(tmp_path: Path) -> None:
    base, repo = workspace(tmp_path)
    env = {"ORCH_SESSION_ID": "abc", "ORCH_MEMBER": "dev"}

    assert cli.run(["state", "set", "--last", "CI green", "--item", "x" * 300], repo, env) == 0

    saved = json.loads((base / "sessions" / "abc.json").read_text(encoding="utf-8"))
    assert saved["last_ok"] is True
    assert len(saved["item"]) == 200

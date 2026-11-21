from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import TYPE_CHECKING, Any

from basicly import consumer_proof

if TYPE_CHECKING:
    import pytest


def test_a_claimed_record_still_at_open_is_found_as_held(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clean = {key: value for key, value in os.environ.items() if not key.startswith("GIT_")}
    subprocess.run(["git", "init", "-q", str(tmp_path)], check=True, env=clean)
    subprocess.run(
        ["git", "-C", str(tmp_path), "config", "basicly.holder", "niksa"], check=True, env=clean
    )
    listed = {
        "in_progress": [{"record": "acme-1", "fields": {"assignee": ""}}],
        "open": [{"record": "acme-2", "fields": {"assignee": "niksa"}}],
    }
    asked: list[str] = []

    real = consumer_proof.worktree.run

    def run(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
        if argv[0] != "uvx":
            return real(argv, **options)
        status = argv[argv.index("--status") + 1]
        asked.append(status)
        body = json.dumps({"records": listed[status]})
        return subprocess.CompletedProcess(argv, 0, body, "")

    monkeypatch.setattr(consumer_proof.worktree, "run", run)

    records = consumer_proof.list_records(tmp_path, {}, "basicly.whl")

    assert asked == ["in_progress", "open"]
    assert consumer_proof.held_record(tmp_path, records) == "acme-2"

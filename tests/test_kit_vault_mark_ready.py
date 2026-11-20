from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.test_kit_vault_cli_wiring import LEDGER, _environment, _kit, _repo, _report
from tests.test_kit_vault_lifecycle import _create, _review, _run
from tests.test_kit_vault_share import bare

__all__ = ["_environment", "bare"]


def _started(root: Path, title: str) -> str:
    record = _create(root, title)
    _run(root, "review", LEDGER, record, "--evidence", json.dumps(_review()))
    _run(root, "claim", record)
    _run(root, "update", LEDGER, record, "--status", "in_progress")
    return record


@pytest.mark.parametrize(
    ("setup", "refusal"),
    [
        ("open", "is open; only an in_progress record waits"),
        ("marked", "pass --again to mark it again"),
    ],
)
def test_vault_mark_ready_refuses_by_name(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, setup: str, refusal: str
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _repo(tmp_path, bare)
    if setup == "open":
        record = _create(root, "A vault task")
    else:
        record = _started(root, "A vault task")
        _run(root, "mark-ready", LEDGER, record)

    done = _kit(root, "mark-ready", LEDGER, record)

    assert done.returncode == 1, done.stdout + done.stderr
    assert refusal in _report(done)["refused"]


def test_vault_mark_ready_refuses_a_record_held_by_another_holder(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _repo(tmp_path, bare)
    record = _started(root, "A vault task")

    done = _kit(root, "mark-ready", LEDGER, record, "--to", "someone-else")

    assert done.returncode == 1, done.stdout + done.stderr
    assert "not by someone-else" in _report(done)["refused"]


def test_vault_list_puts_the_oldest_ready_mark_first(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _repo(tmp_path, bare)
    older, newer = _started(root, "older"), _started(root, "newer")
    _run(root, "mark-ready", LEDGER, older)
    _run(root, "mark-ready", LEDGER, newer)

    listed = _run(root, "list", LEDGER, "--ready-for-delivery")

    assert [row["record"] for row in listed["records"]] == [older, newer]

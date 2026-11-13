from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from tests.test_kit_board_server import server
from tests.test_kit_vault_cli_wiring import (
    LEDGER,
    _clone,
    _environment,
    _hold_the_share_slot,
    _kit,
    _repo,
    _report,
    cli,
    vault_queue,
)
from tests.test_kit_vault_lifecycle import _create, _review, _run
from tests.test_kit_vault_share import bare

if TYPE_CHECKING:
    import pytest

__all__ = ["_environment", "bare"]


def test_pending_writes_are_visible_in_every_read_and_shared_exactly_once(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _repo(tmp_path, bare)
    _hold_the_share_slot(tmp_path, bare)
    before_version = server.api_version(root / LEDGER)
    record = _create(root, "A pending task")
    clone = _clone(tmp_path, bare)
    tried = clone.folder / cli.vault_cli.vault_view.TRIED_NAME
    assert tried.is_file()
    before_fetch = tried.read_bytes()
    _run(root, "review", LEDGER, record, "--evidence", json.dumps(_review()))
    _run(root, "comment", LEDGER, record, "One pending note")

    shown = _run(root, "show", LEDGER, record)
    listed = _run(root, "list", LEDGER)
    ready = _run(root, "ready", LEDGER)
    status, board = server.answer(["list", str(root / LEDGER)], None, read=True)

    assert shown["sharing"] == "pending"
    for report in (listed, ready, board):
        row = next(row for row in report["records"] if row["record"] == record)
        assert row["sharing"] == "pending"
    assert status == 200
    assert server.api_version(root / LEDGER)["version"] != before_version["version"]
    notes = [row for row in shown["comment_log"] if row["text"] == "One pending note"]
    assert len(notes) == 1
    assert tried.read_bytes() == before_fetch
    _run(root, "sync", "--urgent")

    shared = _run(root, "show", LEDGER, record)

    assert shared.get("sharing") != "pending"
    assert [row["id"] for row in shared["comment_log"] if row["text"] == "One pending note"] == [
        notes[0]["id"]
    ]
    assert not list((root / LEDGER).glob("*.jsonl"))


def test_a_second_clone_sees_an_own_queue_write_only_after_it_is_shared(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, bare)
    control = _create(root, "A published control")
    _run(root, "sync", "--urgent")
    _hold_the_share_slot(tmp_path, bare)
    pending = _create(root, "A private pending task")
    other = _repo(tmp_path / "other", bare)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "other-state"))
    assert _run(other, "show", LEDGER, control)["record"] == control

    absent = _kit(other, "show", LEDGER, pending)

    assert absent.returncode == 1 and _report(absent)["found"] is False
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    _run(root, "sync", "--urgent")
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "other-state"))
    _run(other, "fetch", "--vault", str(bare))
    shared = _run(other, "show", LEDGER, pending)
    assert shared["record"] == pending and shared.get("sharing") != "pending"


def test_a_pending_claim_never_grants_canonical_ownership(tmp_path: Path, bare: Path) -> None:
    root = _repo(tmp_path, bare)
    record = _create(root, "A task with an unsettled claim")
    _run(root, "sync", "--urgent")
    clone = _clone(tmp_path, bare)
    vault_queue.write(
        clone,
        [{"kind": "claim", "record": record}],
        state=tmp_path / "state",
        origin=vault_queue.Origin("s1", False, cli.events.wall_clock()),
    )

    shown = _run(root, "show", LEDGER, record)

    assert shown["holder"] is None
    assert shown["sharing"] == "pending"


def test_two_sessions_merge_the_same_note_by_content_identity_and_report_it(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    root = _repo(tmp_path, bare)
    record = _create(root, "A shared note identity")
    first = _run(root, "comment", record, "--text", "One agreed fact")
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "another-session")
    second = _run(root, "comment", record, "--text", "One agreed fact")

    assert first["queued"] == 1
    assert second["queued"] == 0 and second["merged"] is True and second["events"] == []
    _run(root, "sync", "--urgent")
    shown = _run(root, "show", LEDGER, record)
    assert [row["text"] for row in shown["comment_log"]] == ["One agreed fact"]


def test_legacy_native_comments_are_counted_and_named_without_changing_the_queue(
    tmp_path: Path, bare: Path
) -> None:
    root = _repo(tmp_path, bare)
    record = _create(root, "A legacy notice control")
    _run(root, "sync", "--urgent")
    assert _run(root, "show", LEDGER, record)["record"] == record
    clone = _clone(tmp_path, bare)
    vault_queue.write(
        clone,
        [
            vault_queue.comment(record, "same text"),
            vault_queue.comment(record, "same text"),
            vault_queue.comment("other-a1", "another project"),
        ],
        state=tmp_path / "state",
        origin=vault_queue.Origin("s1", False, cli.events.wall_clock()),
    )
    queue = clone.folder / vault_queue.QUEUE_NAME
    assert queue.is_file()
    before = queue.read_bytes()
    raw = vault_queue.pending(queue)

    refused = _kit(root, "show", LEDGER, record)

    assert refused.returncode == 1
    message = _report(refused)["refused"]
    assert "2 legacy vault comments are not shown" in message
    assert "basicly-pvdo68d" in message and "\n" not in message
    assert queue.read_bytes() == before
    _run(root, "sync", "--urgent")
    vault_queue.requeue(clone, raw)
    duplicate = _kit(root, "show", LEDGER, record)
    assert duplicate.returncode == 1
    assert _report(duplicate)["refused"] == message

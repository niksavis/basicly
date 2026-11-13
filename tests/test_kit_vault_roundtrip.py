from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

from basicly import owned_store
from tests.test_kit_vault_view import _carry, _push, _record
from tests.test_kit_vault_write import (
    CONTEXT,
    SEED_PREFIX,
    VAULT_PREFIX,
    Repo,
    _seeded,
    _share,
    bare,
    commands,
    events,
    vault_write,
    writers,
)

__all__ = ["bare"]
VAULT_ONLY = f"{VAULT_PREFIX}-v1"


@pytest.fixture(autouse=True)
def _environment(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    markers = (*writers.AGENT_MARKERS, writers.CLAUDE_CODE_MARKER)
    for name in ("CI", "BASICLY_TRACKER_EPHEMERAL", "XDG_STATE_HOME", *markers):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("BASICLY_TRACKER_STATE", str(tmp_path / "state"))
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "s1")


def _land(repo: Repo, draft: object) -> list:
    write = vault_write.Write(repo.ledger, CONTEXT, None, dict(os.environ))
    return vault_write.land(write, [draft])


def _carried(event: object) -> dict:
    return json.loads(events.to_json(event))


def _shown(repo: Repo, record: str) -> list:
    found = events.read_events(owned_store.present_ledger(repo.root))[0]
    return [event for event in found if event.record == record]


def test_a_write_to_a_record_only_the_vault_holds_mints_its_seq_and_id_from_the_vault_view(
    tmp_path: Path, bare: Path
) -> None:
    held = _record(VAULT_ONLY)
    _push(tmp_path, bare, "m1", _carry(held))
    repo = _seeded(tmp_path, bare)
    priority = events.Draft(VAULT_ONLY, events.KIND_FIELD, {"name": "priority", "value": 1})

    (noted,) = _land(repo, priority)
    _share(repo)
    (reopened,) = _land(repo, events.Draft(VAULT_ONLY, events.KIND_STATUS, {"status": "open"}))
    _share(repo)

    assert noted.seq == max(event.seq for event in _shown(repo, VAULT_ONLY)[:2]) + 1
    assert reopened.seq == noted.seq + 1
    assert reopened.id not in {event["id"] for event in held}
    assert [event.id for event in _shown(repo, VAULT_ONLY)] == [
        *(event["id"] for event in held),
        noted.id,
        reopened.id,
    ]
    assert VAULT_ONLY not in events.fold(events.read_events(repo.ledger)[0]).records


def test_a_record_written_through_the_vault_reads_back_unchanged_through_the_view(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)

    written = commands.create_root(
        repo.ledger, {"title": "vault born"}, prefix=SEED_PREFIX, context=CONTEXT
    )
    _share(repo)
    record = written[0].record
    (noted,) = _land(repo, events.Draft(record, events.KIND_COMMENT, {"text": "seen"}))
    _share(repo)

    assert record.startswith(f"{VAULT_PREFIX}-")
    assert [_carried(event) for event in _shown(repo, record)] == [
        _carried(event) for event in (*written, noted)
    ]


def test_a_claimed_record_mints_past_the_view_holder_line_without_a_gap(
    tmp_path: Path, bare: Path
) -> None:
    repo = _seeded(tmp_path, bare)
    written = commands.create_root(
        repo.ledger, {"title": "held"}, prefix=SEED_PREFIX, context=CONTEXT
    )
    _share(repo)
    record = written[0].record
    commands.claim(repo.ledger, record, "someone", context=CONTEXT)
    _share(repo)
    priority = events.Draft(record, events.KIND_FIELD, {"name": "priority", "value": 1})

    (noted,) = _land(repo, events.Draft(record, events.KIND_COMMENT, {"text": "held"}))
    _share(repo)
    (raised,) = _land(repo, priority)
    _share(repo)
    repeated = _land(repo, priority)
    _share(repo)

    carried = [_carried(event) for event in _shown(repo, record) if event.actor != "vault"]
    assert carried == [_carried(event) for event in (*written, noted, raised)]
    assert repeated == []

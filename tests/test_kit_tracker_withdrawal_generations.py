from __future__ import annotations

import shutil
from pathlib import Path

import pytest

from basicly import tracker


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    source = Path(__file__).parent.parent / tracker.KIT_TRACKER_DIR
    shutil.copytree(source, tmp_path / tracker.KIT_TRACKER_DIR)
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode="owned"\n', encoding="utf-8")
    return tmp_path


@pytest.mark.parametrize("kind", ["comment", "field", "gate"])
def test_recurrence_reserves_withdrawn_ids(repo: Path, kind: str) -> None:
    events = tracker.kit(repo).events
    recurrence = tracker.kit(repo, "recurrence")
    ledger = tracker.ledger_dir(repo)
    payloads = {
        "comment": {"text": "same prose"},
        "field": {"name": "priority", "value": 1, "extra": "same prose"},
        "gate": {"gate": "verify", "provider": "engine", "passed": True, "extra": "same prose"},
    }
    draft = events.Draft("demo-a", kind, payloads[kind])
    written = events.append(
        ledger,
        [events.Draft("demo-a", "created", {"title": "safe"}), draft],
        writer="",
    )
    retired = written[1].id
    events.withdraw(ledger, retired, reason="remove first occurrence")

    resolved = recurrence.at_the_generation_this_write_needs(
        events, ledger, [draft], repeat=kind == "comment"
    )
    appended = events.append(ledger, resolved, writer="")

    assert len(appended) == 1
    assert appended[0].id != retired
    assert resolved[0].generation == 2
    assert tracker.kit(repo, "fsck").check(ledger).clean


def test_direct_append_refuses_a_retired_id_without_writing(repo: Path) -> None:
    events = tracker.kit(repo).events
    ledger = tracker.ledger_dir(repo)
    draft = events.Draft("demo-a", "comment", {"text": "same prose"})
    written = events.append(
        ledger, [events.Draft("demo-a", "created", {"title": "safe"}), draft], writer=""
    )
    events.withdraw(ledger, written[1].id, reason="remove first occurrence")
    before = {path: path.read_bytes() for path in events.ledger_paths(ledger)}

    with pytest.raises(events.InvalidEventError, match="retired"):
        events.append(ledger, [events.Draft("demo-a", "comment", {"text": "new"}), draft])

    assert {path: path.read_bytes() for path in events.ledger_paths(ledger)} == before
    assert tracker.kit(repo, "fsck").check(ledger).clean


def test_direct_append_keeps_live_idempotence_after_a_withdrawal(repo: Path) -> None:
    events = tracker.kit(repo).events
    ledger = tracker.ledger_dir(repo)
    draft = events.Draft("demo-a", "comment", {"text": "live prose"})
    written = events.append(
        ledger,
        [
            events.Draft("demo-a", "created", {"title": "safe"}),
            events.Draft("demo-a", "comment", {"text": "withdrawn prose"}),
            draft,
        ],
        writer="",
    )
    events.withdraw(ledger, written[1].id, reason="remove first occurrence")
    before = {path: path.read_bytes() for path in events.ledger_paths(ledger)}

    assert events.append(ledger, [draft], writer="") == []

    assert {path: path.read_bytes() for path in events.ledger_paths(ledger)} == before
    assert tracker.kit(repo, "fsck").check(ledger).clean


def test_gate_recurrence_keeps_the_current_surviving_generation_idempotent(repo: Path) -> None:
    events = tracker.kit(repo).events
    recurrence = tracker.kit(repo, "recurrence")
    ledger = tracker.ledger_dir(repo)
    draft = events.Draft(
        "demo-a", "gate", {"gate": "verify", "provider": "engine", "passed": True, "extra": "prose"}
    )
    written = events.append(
        ledger, [events.Draft("demo-a", "created", {"title": "safe"}), draft], writer=""
    )
    events.withdraw(ledger, written[1].id, reason="remove first occurrence")
    resolved = recurrence.at_the_generation_this_write_needs(events, ledger, [draft])
    events.append(ledger, resolved, writer="")
    before = {path: path.read_bytes() for path in events.ledger_paths(ledger)}

    repeated = recurrence.at_the_generation_this_write_needs(events, ledger, [draft])

    assert events.append(ledger, repeated, writer="") == []
    assert {path: path.read_bytes() for path in events.ledger_paths(ledger)} == before
    assert tracker.kit(repo, "fsck").check(ledger).clean

from __future__ import annotations

import json
import shutil
from pathlib import Path
from typing import Any

import pytest

from basicly import tracker
from basicly.owned_store import TrackerDivergenceError


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    source = Path(__file__).parent.parent / tracker.KIT_TRACKER_DIR
    shutil.copytree(source, tmp_path / tracker.KIT_TRACKER_DIR)
    (tmp_path / "basicly.toml").write_text('[tracker]\nmode="owned"\n', encoding="utf-8")
    return tmp_path


def _withdraw_first(repo: Path, payload: dict) -> tuple[Any, Any, Any]:
    events = tracker.kit(repo).events
    ledger = tracker.ledger_dir(repo)
    written = events.append(
        ledger,
        [
            events.Draft("demo-a", "created", {"title": "safe"}),
            events.Draft("demo-a", "comment", payload),
            events.Draft("demo-a", "comment", payload, generation=2),
        ],
        writer="",
    )
    events.withdraw(ledger, written[1].id, reason="remove first occurrence")
    return events, written[1], written[2]


@pytest.mark.parametrize("actor_only", [False, True])
def test_scrub_preserves_surviving_generation_after_withdrawal(
    repo: Path, actor_only: bool
) -> None:
    events, retired, survivor = _withdraw_first(repo, {"text": "repeated prose"})
    paths = events.ledger_paths(tracker.ledger_dir(repo))
    if actor_only:
        for path in paths:
            lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
            for line in lines:
                line["actor"] = "Private Person"
            path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    before = {path: path.read_bytes() for path in paths}
    assert tracker.kit(repo, "fsck").check(tracker.ledger_dir(repo)).clean

    changed = tracker.scrub_ledger(repo, display_name="Private Person")

    stored, _ = events.read_events(tracker.ledger_dir(repo))
    assert survivor.id in {event.id for event in stored}
    assert retired.id not in {event.id for event in stored}
    assert tracker.kit(repo, "fsck").check(tracker.ledger_dir(repo)).clean
    if actor_only:
        assert changed == len(stored)
    else:
        assert changed == 0
        assert {path: path.read_bytes() for path in paths} == before


def test_scrub_reserves_unchanged_events_when_redaction_merges_payloads(repo: Path) -> None:
    events = tracker.kit(repo).events
    ledger = tracker.ledger_dir(repo)
    clean = {"text": "<redacted:git-user-name>"}
    written = events.append(
        ledger,
        [
            events.Draft("demo-a", "created", {"title": "safe"}),
            events.Draft("demo-a", "comment", {"text": "Private Person"}),
            events.Draft("demo-a", "comment", clean),
        ],
        writer="",
    )

    assert tracker.scrub_ledger(repo, display_name="Private Person") == 1

    stored, _ = events.read_events(ledger)
    comments = [event for event in stored if event.kind == "comment"]
    assert next(event for event in comments if event.seq == written[2].seq).id == written[2].id
    assert len({event.id for event in comments}) == 2
    assert comments[0].payload == comments[1].payload
    assert tracker.kit(repo, "fsck").check(ledger).clean


def test_scrub_remaps_withdrawal_audit_after_redacting_the_replacement(repo: Path) -> None:
    events, retired, _ = _withdraw_first(repo, {"text": "repeated prose"})
    ledger = tracker.ledger_dir(repo)
    paths = events.ledger_paths(ledger)
    for path in paths:
        lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
        for line in lines:
            if events.WITHDRAWN_FROM in line["payload"]:
                line["payload"]["extra"] = "Private Person"
                previous = line["id"]
                line["id"] = events.event_id_for(line["record"], line["kind"], line["payload"])
                for candidate in lines:
                    if candidate["payload"].get(events.WITHDRAWN_TARGET) == previous:
                        candidate["payload"][events.WITHDRAWN_TARGET] = line["id"]
                        candidate["id"] = events.event_id_for(
                            candidate["record"], candidate["kind"], candidate["payload"]
                        )
        path.write_text("".join(json.dumps(line) + "\n" for line in lines), encoding="utf-8")
    assert tracker.kit(repo, "fsck").check(ledger).clean

    assert tracker.scrub_ledger(repo, display_name="Private Person") == 2

    stored, _ = events.read_events(ledger)
    replacement = next(event for event in stored if events.WITHDRAWN_FROM in event.payload)
    audit = next(event for event in stored if event.kind == events.KIND_WITHDRAWN)
    assert replacement.payload[events.WITHDRAWN_FROM] == retired.id
    assert audit.payload[events.WITHDRAWN_TARGET] == replacement.id
    assert tracker.kit(repo, "fsck").check(ledger).clean


def test_scrub_refuses_a_generation_outside_the_kit_bound_without_writes(repo: Path) -> None:
    events = tracker.kit(repo).events
    ledger = tracker.ledger_dir(repo)
    events.append(
        ledger,
        [events.Draft("demo-a", "created", {"title": "Private Person"}, generation=99)],
        writer="",
    )
    before = {path: path.read_bytes() for path in events.ledger_paths(ledger)}
    assert not tracker.kit(repo, "fsck").check(ledger).clean

    with pytest.raises(TrackerDivergenceError, match="does not re-mint"):
        tracker.scrub_ledger(repo, display_name="Private Person")

    assert {path: path.read_bytes() for path in events.ledger_paths(ledger)} == before


def test_scrub_does_not_reuse_a_retired_id_when_payloads_merge(repo: Path) -> None:
    events, retired, survivor = _withdraw_first(repo, {"text": "<redacted:git-user-name>"})
    ledger = tracker.ledger_dir(repo)
    events.append(
        ledger, [events.Draft("demo-a", "comment", {"text": "Private Person"})], writer=""
    )

    assert tracker.scrub_ledger(repo, display_name="Private Person") == 1

    stored, _ = events.read_events(ledger)
    assert retired.id not in {event.id for event in stored}
    assert survivor.id in {event.id for event in stored}
    assert len({event.id for event in stored}) == 5
    assert tracker.kit(repo, "fsck").check(ledger).clean

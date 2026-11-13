from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from pathlib import Path

import pytest

KIT = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "tracker"
SPEC = importlib.util.spec_from_file_location(
    "basicly_tracker_kit_vault_canonical", KIT / "vault_canonical.py"
)
assert SPEC and SPEC.loader
canonical = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = canonical
SPEC.loader.exec_module(canonical)
events = canonical.events
RECORD = "demo-abcd"
PRIVATE = "private-person"


def _redact(text: str) -> str:
    return text.replace(PRIVATE, "contributor")


def _source(tmp_path: Path) -> list:
    events.append(
        tmp_path,
        [
            events.Draft(RECORD, "created", {"title": PRIVATE, "assignee": PRIVATE}),
            events.Draft(RECORD, "note", {"text": "Keep this public note."}),
            events.Draft(RECORD, "field", {"name": "assignee", "value": "second-holder"}),
            events.Draft(RECORD, "status", {"status": "closed"}),
        ],
        actor=PRIVATE,
        clock=lambda: 1000.0,
    )
    return events.read_events(tmp_path)[0]


def test_identities_keep_record_ids_and_unchanged_payload_event_ids(tmp_path: Path) -> None:
    found = _source(tmp_path)
    made = canonical.canonicalize(found, redactor=_redact, redact_holders=True)
    assert {event.record for event in made.events} == {RECORD}
    assert made.events[0].id != found[0].id
    assert [event.id for event in made.events[1:]] == [event.id for event in found[1:]]
    assert made.events[0].payload["assignee"] == "contributor"
    assert len(made.events) == len(found)
    assert events.fold(made.events).records[RECORD].status == "closed"
    assert events.read_events(tmp_path)[0] == found


def test_fsck_accepts_reminted_payloads_with_unchanged_checker(tmp_path: Path) -> None:
    found = _source(tmp_path / "source")
    made = canonical.canonicalize(found, redactor=_redact, redact_holders=True)
    target = tmp_path / "target"
    target.mkdir()
    (target / events.INITIAL_LOG_NAME).write_text(
        "".join(events.to_json(e) + "\n" for e in made.events)
    )
    assert canonical.fsck.check(target).exit_code == 0
    broken = dataclasses.replace(made.events[0], id=found[0].id)
    (target / events.INITIAL_LOG_NAME).write_text(events.to_json(broken) + "\n")
    assert "unminted-id" in {finding.kind for finding in canonical.fsck.check(target).findings}


def test_deterministic_canonical_bytes_ignore_source_iteration_order(tmp_path: Path) -> None:
    found = _source(tmp_path)
    first = canonical.canonicalize(found, redactor=_redact, redact_holders=True)
    second = canonical.canonicalize(list(reversed(found)), redactor=_redact, redact_holders=True)
    assert [events.to_json(e) for e in first.events] == [events.to_json(e) for e in second.events]
    assert first.mapping == second.mapping


def test_references_follow_reminted_ids_in_payloads_and_extra_fields(tmp_path: Path) -> None:
    found = _source(tmp_path)
    cited = found[0].id
    events.append(
        tmp_path,
        [events.Draft(RECORD, "note", {"text": f"See `{cited}` for the source."})],
        actor="contributor",
        clock=lambda: 2000.0,
    )
    found = events.read_events(tmp_path)[0]
    found[-1] = dataclasses.replace(found[-1], extra={"citation": cited})
    made = canonical.canonicalize(found, redactor=_redact, redact_holders=True)
    assert made.events[0].id in made.events[-1].payload["text"]
    assert made.events[-1].extra["citation"] == made.events[0].id
    assert cited not in "".join(events.to_json(e) for e in made.events)
    assert [event.seq for event in made.events] == list(range(1, len(found) + 1))


def test_report_names_touched_fields_without_removed_values(tmp_path: Path) -> None:
    made = canonical.canonicalize(_source(tmp_path), redactor=_redact, redact_holders=True)
    assert made.reminted == 1
    assert set(made.touched) == {"actor", "payload.title", "payload.assignee"}
    assert PRIVATE not in repr(made)
    assert PRIVATE not in json.dumps(made.mapping)


@pytest.mark.parametrize("redact_holders", [False, True])
def test_holder_history_uses_one_explicit_redaction_choice(
    tmp_path: Path, redact_holders: bool
) -> None:
    found = _source(tmp_path)
    made = canonical.canonicalize(found, redactor=_redact, redact_holders=redact_holders)
    assert made.events[0].payload["assignee"] == ("contributor" if redact_holders else PRIVATE)
    assert made.events[0].payload["title"] == "contributor"
    assert made.events[0].actor == "contributor"
    assert len(made.events) == len(found)


def test_fsck_refuses_invalid_source_before_normalization(tmp_path: Path) -> None:
    found = _source(tmp_path)
    found[0] = dataclasses.replace(found[0], payload={"title": "rewritten without reminting"})
    with pytest.raises(canonical.CanonicalError, match="unminted-id"):
        canonical.canonicalize(found, redact_holders=True)


def _reviewed(folder: Path) -> list:
    process = canonical.process
    events.append(
        folder,
        [
            events.Draft(
                RECORD,
                "created",
                {
                    "title": PRIVATE,
                    "issue_type": "task",
                    "description": (
                        "When a record is saved, I want its title, so I can identify it."
                    ),
                    "acceptance_criteria": "When a record is read, show shall return its title.",
                    "requirements": "Use the existing ledger.",
                },
            )
        ],
        actor=PRIVATE,
        clock=lambda: 1000.0,
    )
    found = events.read_events(folder)[0]
    check = {
        "criterion": "When a record is read, show shall return its title.",
        "command": ["python3", "check_title.py"],
        "expected": "The saved title appears.",
    }
    draft = process.review_draft(found, RECORD, {"checks": [check]}, PRIVATE)
    events.append(folder, [draft], actor=PRIVATE, clock=lambda: 2000.0)
    found = events.read_events(folder)[0]
    completed = {key: value for key, value in check.items() if key != "expected"}
    completed.update({"result": "The title appeared.", "exit_code": 0})
    draft = process.confirmation_draft(found, RECORD, {"checks": [completed]}, PRIVATE)
    events.append(
        folder,
        [draft, events.Draft(RECORD, "status", {"status": "closed"})],
        actor=PRIVATE,
        clock=lambda: 3000.0,
    )
    return events.read_events(folder)[0]


def test_evidence_preserves_current_review_confirmation_and_closed_state(tmp_path: Path) -> None:
    found = _reviewed(tmp_path)
    original = canonical.process.confirmation_report(found, RECORD)
    assert original["review_current"] and original["confirmation_current"]
    assert not original["owed"]
    made = canonical.canonicalize(found, redactor=_redact, redact_holders=True)
    report = canonical.process.confirmation_report(made.events, RECORD)
    assert report["review_current"] and report["confirmation_current"]
    assert report["revision"] != original["revision"]
    assert not report["owed"]
    state = events.fold(made.events).records[RECORD]
    assert state.status == "closed"
    assert canonical.process.closing_owed(made.events, RECORD, state.fields) == ()
    assert original["revision"] not in "".join(events.to_json(e) for e in made.events)


def test_evidence_never_makes_stale_review_or_confirmation_current(tmp_path: Path) -> None:
    _reviewed(tmp_path)
    events.append(
        tmp_path,
        [events.Draft(RECORD, "field", {"name": "title", "value": "Changed title"})],
        actor=PRIVATE,
        clock=lambda: 4000.0,
    )
    found = events.read_events(tmp_path)[0]
    original = canonical.process.confirmation_report(found, RECORD)
    assert not original["review_current"] and not original["confirmation_current"]
    made = canonical.canonicalize(found, redactor=_redact, redact_holders=True)
    report = canonical.process.confirmation_report(made.events, RECORD)
    assert not report["review_current"] and not report["confirmation_current"]
    assert report["owed"]


def test_references_preserve_withdrawal_history_without_private_retired_ids(tmp_path: Path) -> None:
    found = _source(tmp_path)
    retired = found[1].id
    events.withdraw(tmp_path, retired, reason="The note was withdrawn.")
    source = events.read_events(tmp_path)[0]
    assert canonical.fsck.check(tmp_path).exit_code == 0
    made = canonical.canonicalize(source, redactor=_redact, redact_holders=True)
    assert len(made.events) == len(source)
    assert retired not in "".join(events.to_json(event) for event in made.events)
    assert any(event.kind == events.KIND_WITHDRAWN for event in made.events)
    canonical.check(made.events)


def test_references_keep_loss_acknowledgment_as_history_after_gapless_normalization(
    tmp_path: Path,
) -> None:
    found = _source(tmp_path)
    log = tmp_path / events.INITIAL_LOG_NAME
    log.write_text("".join(events.to_json(event) + "\n" for event in found if event.seq != 2))
    canonical.recovery.acknowledge(
        tmp_path,
        RECORD,
        canonical.recovery.Request(
            (2,), "The note was lost before this import.", ("archived audit",)
        ),
    )
    source = events.read_events(tmp_path)[0]
    assert canonical.fsck.check(tmp_path).exit_code == 0
    original = canonical.recovery.history_digest([event for event in source if event.seq <= 4])
    made = canonical.canonicalize(source, redactor=_redact, redact_holders=True)
    assert [event.seq for event in made.events] == list(range(1, len(source) + 1))
    assert made.events[-1].payload["artifact"] == "historical-sequence-loss"
    assert made.events[-1].payload["body"]["missing_sequences"] == [2]
    assert original not in "".join(events.to_json(event) for event in made.events)
    assert len(made.events) == len(source)

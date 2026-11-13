from __future__ import annotations

import json
from pathlib import Path

from tests.test_kit_vault_canonical import PRIVATE, RECORD, _redact, canonical, events
from tests.tracker_process_fixture import INVEST


def _full_review(folder: Path) -> list:
    fields = {
        "title": PRIVATE,
        "issue_type": "feature",
        "description": "When a record is saved, I want its title, so I can identify it.",
        "acceptance_criteria": "When a record is read, show shall return its title.",
        "requirements": "Use the existing ledger.",
    }
    events.append(
        folder,
        [
            events.Draft(RECORD, "created", fields),
            events.Draft(RECORD, "note", {"text": "The historically lost note."}),
            events.Draft(RECORD, "comment", {"text": "We agreed to preserve the public title."}),
            events.Draft("demo-zzzz", "created", {"title": PRIVATE}),
        ],
        actor=PRIVATE,
        clock=lambda: 1000.0,
    )
    found = events.read_events(folder)[0]
    (folder / events.INITIAL_LOG_NAME).write_text(
        "".join(
            events.to_json(event) + "\n"
            for event in found
            if not (event.record == RECORD and event.seq == 2)
        )
    )
    canonical.recovery.acknowledge(
        folder,
        RECORD,
        canonical.recovery.Request((2,), "A historical note was lost.", ("archived audit",)),
    )
    events.append(
        folder,
        [events.Draft(RECORD, "edge", {"target": "demo-zzzz", "edge_type": "related"})],
        actor=PRIVATE,
        clock=lambda: 2000.0,
    )
    found = events.read_events(folder)[0]
    checks = [
        {
            "criterion": fields["acceptance_criteria"],
            "command": ["python3", "check_title.py"],
            "expected": "The title appears.",
        }
    ]
    draft = canonical.process.review_draft(
        found, RECORD, {"checks": checks, "invest": INVEST, "conversation": [3]}, PRIVATE
    )
    events.append(folder, [draft], actor=PRIVATE, clock=lambda: 3000.0)
    found = events.read_events(folder)[0]
    completed = [
        {
            "criterion": checks[0]["criterion"],
            "command": checks[0]["command"],
            "result": "The title appeared.",
            "exit_code": 0,
        }
    ]
    draft = canonical.process.confirmation_draft(found, RECORD, {"checks": completed}, PRIVATE)
    events.append(
        folder,
        [draft, events.Draft(RECORD, "status", {"status": "closed"})],
        actor=PRIVATE,
        clock=lambda: 4000.0,
    )
    return events.read_events(folder)[0]


def test_evidence_rewrites_conversation_sequences_and_dependency_revisions(tmp_path: Path) -> None:
    source = _full_review(tmp_path)
    original = canonical.process.confirmation_report(source, RECORD)
    assert original["review_current"] and original["confirmation_current"] and not original["owed"]
    made = canonical.canonicalize(source, redactor=_redact, redact_holders=True)
    report = canonical.process.confirmation_report(made.events, RECORD)
    assert report["review_current"] and report["confirmation_current"] and not report["owed"]
    assert report["review"]["conversation"] == [2]
    assert report["revision"] != original["revision"]
    assert events.fold(made.events).records[RECORD].status == "closed"
    assert len(made.events) == len(source)
    original_target = next(event for event in source if event.record == "demo-zzzz")
    migrated_target = next(event for event in made.events if event.record == "demo-zzzz")
    assert original_target.id != migrated_target.id
    assert migrated_target.payload["title"] == "contributor"


def test_invalid_conversation_never_becomes_valid_when_a_gap_is_removed(tmp_path: Path) -> None:
    source = _full_review(tmp_path)
    report = canonical.process.confirmation_report(source, RECORD)
    invalid = {**report["review"], "conversation": [2]}
    events.append(
        tmp_path,
        [
            events.Draft(
                RECORD,
                "field",
                {
                    "name": canonical.process.REVIEW_FIELD,
                    "value": json.dumps(invalid, sort_keys=True),
                    canonical.process.VERSION_FIELD: canonical.process.VERSION,
                },
            )
        ],
        actor=PRIVATE,
        clock=lambda: 5000.0,
    )
    source = events.read_events(tmp_path)[0]
    assert (
        canonical.process.CONVERSATION_HEADING
        in canonical.process.confirmation_report(source, RECORD)["owed"]
    )
    made = canonical.canonicalize(source, redactor=_redact, redact_holders=True)
    report = canonical.process.confirmation_report(made.events, RECORD)
    assert not report["review_current"]
    assert canonical.process.CONVERSATION_HEADING in report["owed"]

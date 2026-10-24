from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.kit_deployment_helpers import events, fsck, snapshot

RECORD = "demo-a"
PRIVATE = "private prose for the withdrawal fixture"


def _field(ledger: Path, name: str, value: object) -> Any:
    structured = isinstance(value, (dict, list))
    written = events.append(
        ledger,
        [
            events.Draft(RECORD, events.KIND_CREATED, {"title": "safe"}),
            events.Draft(
                RECORD,
                events.KIND_FIELD,
                {"name": name, "value": "legacy" if structured else value},
            ),
        ],
        writer="",
    )[-1]
    if structured:
        payload = {"name": name, "value": value}
        written = replace(
            written, payload=payload, id=events.event_id_for(RECORD, events.KIND_FIELD, payload)
        )
        log = ledger / events.INITIAL_LOG_NAME
        lines = log.read_text(encoding="utf-8").splitlines()
        lines[-1] = events.to_json(written)
        log.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return written


def _bytes(ledger: Path) -> dict[str, bytes]:
    return {path.name: path.read_bytes() for path in events.ledger_paths(ledger)}


@pytest.mark.parametrize(
    "name",
    [
        "title",
        "description",
        "acceptance_criteria",
        "requirements",
        "close_reason",
        "design",
        "notes",
    ],
)
def test_prose_field_withdrawal_removes_text_and_preserves_audited_history(
    tmp_path: Path, name: str
) -> None:
    original = _field(tmp_path, name, PRIVATE)
    assert PRIVATE in b"".join(_bytes(tmp_path).values()).decode("utf-8")

    trail = events.withdraw(tmp_path, original.id, reason="remove fixture disclosure")

    stored = events.read_events(tmp_path)[0]
    withdrawn = next(one for one in stored if one.payload.get(events.WITHDRAWN_FROM) == original.id)
    assert PRIVATE not in b"".join(_bytes(tmp_path).values()).decode("utf-8")
    assert withdrawn.id != original.id
    assert withdrawn.id == events.event_id_for(RECORD, events.KIND_FIELD, withdrawn.payload)
    assert withdrawn.seq == original.seq
    assert withdrawn.payload["name"] == name
    assert withdrawn.payload["value"] == events.WITHDRAWN_PLACEHOLDER
    assert withdrawn.payload["value_withdrawn"] is True
    folded = events.fold(stored)
    assert folded.records[RECORD].fields[name] == events.WITHDRAWN_PLACEHOLDER
    assert folded.withdrawals[0].target == withdrawn.id == trail.payload["target"]
    assert folded.withdrawals[0].reason == "remove fixture disclosure"
    assert fsck.check(tmp_path).clean


@pytest.mark.parametrize(
    "name,value",
    [
        ("priority", 2),
        ("labels", ["refine"]),
        ("issue_type", "task"),
        ("close_resolution", "completed"),
        ("assignee", "operator"),
        ("external_ref", "worktree:lane:harness/lane"),
        ("process_review", {"checks": []}),
        ("process_confirmation", {"checks": []}),
        ("unknown", PRIVATE),
        ("description", {"machine": "state"}),
        ("description", 17),
    ],
)
def test_structural_unknown_and_non_string_fields_are_refused_without_mutation(
    tmp_path: Path, name: str, value: object
) -> None:
    original = _field(tmp_path, name, value)
    before = _bytes(tmp_path)

    with pytest.raises(events.InvalidEventError, match="no free text to withdraw"):
        events.withdraw(tmp_path, original.id, reason="cannot discard machine state")

    assert _bytes(tmp_path) == before


def test_a_declared_template_prose_field_can_be_withdrawn(tmp_path: Path) -> None:
    (tmp_path / "template.json").write_text('{"sections":["## Risks"]}', encoding="utf-8")
    original = _field(tmp_path, "risks", PRIVATE)

    events.withdraw(tmp_path, original.id, reason="remove fixture disclosure")

    assert events.fold(events.read_events(tmp_path)[0]).records[RECORD].fields["risks"] == ""
    assert fsck.check(tmp_path).clean


@pytest.mark.parametrize("name", ["priority", "dates"])
def test_a_template_cannot_make_a_structural_field_withdrawable(tmp_path: Path, name: str) -> None:
    (tmp_path / "template.json").write_text(
        json.dumps({"sections": [f"## {name.title()}"]}), encoding="utf-8"
    )
    original = _field(tmp_path, name, "2")
    before = _bytes(tmp_path)

    with pytest.raises(events.InvalidEventError, match="no free text to withdraw"):
        events.withdraw(tmp_path, original.id, reason="cannot discard machine state")

    assert _bytes(tmp_path) == before


@pytest.mark.parametrize("reason", ["", " ", None, 3])
def test_invalid_audit_reasons_refuse_before_removing_any_content(
    tmp_path: Path, reason: Any
) -> None:
    original = events.append(
        tmp_path,
        [
            events.Draft(RECORD, events.KIND_CREATED, {"title": "safe"}),
            events.Draft(RECORD, events.KIND_COMMENT, {"text": PRIVATE}),
        ],
        writer="",
    )[-1]
    before = _bytes(tmp_path)

    with pytest.raises(events.InvalidEventError, match="reason"):
        events.withdraw(tmp_path, original.id, reason=reason)

    assert _bytes(tmp_path) == before
    assert fsck.check(tmp_path).clean


@pytest.mark.parametrize("changed", ["reason", "target"])
def test_a_redactor_cannot_destroy_the_audit_after_content_was_removed(
    tmp_path: Path, changed: str
) -> None:
    original = events.append(
        tmp_path,
        [
            events.Draft(RECORD, events.KIND_CREATED, {"title": "safe"}),
            events.Draft(RECORD, events.KIND_COMMENT, {"text": PRIVATE}),
        ],
        writer="",
    )[-1]
    before = _bytes(tmp_path)

    def redact(text: str) -> str:
        if changed == "reason" and text == "remove disclosure":
            return ""
        return "another-target" if changed == "target" and "#ev-" in text else text

    with pytest.raises(events.InvalidEventError):
        events.withdraw(tmp_path, original.id, reason="remove disclosure", redact=redact)

    assert _bytes(tmp_path) == before
    assert fsck.check(tmp_path).clean


def test_a_later_safe_field_value_survives_withdrawal_of_its_older_value(tmp_path: Path) -> None:
    original = _field(tmp_path, "description", PRIVATE)
    events.append(
        tmp_path,
        [
            events.Draft(
                RECORD, events.KIND_FIELD, {"name": "description", "value": "safe replacement"}
            )
        ],
        writer="",
    )

    events.withdraw(tmp_path, original.id, reason="remove older disclosure")

    assert (
        events.fold(events.read_events(tmp_path)[0]).records[RECORD].fields["description"]
        == "safe replacement"
    )
    assert PRIVATE not in b"".join(_bytes(tmp_path).values()).decode("utf-8")
    assert fsck.check(tmp_path).clean


def test_duplicate_targets_refuse_until_explicit_compaction_removes_the_extra_copy(
    tmp_path: Path,
) -> None:
    written = events.append(
        tmp_path,
        [
            events.Draft(RECORD, events.KIND_CREATED, {"title": "safe"}),
            events.Draft(RECORD, events.KIND_COMMENT, {"text": PRIVATE}),
        ],
        writer="",
    )
    log = tmp_path / events.INITIAL_LOG_NAME
    copied = next(
        line for line in log.read_text().splitlines() if json.loads(line)["id"] == written[-1].id
    )
    events.pending_path(tmp_path, "duplicate").write_text(copied + "\n", encoding="utf-8")
    before = _bytes(tmp_path)

    with pytest.raises(events.InvalidEventError, match="compact"):
        events.withdraw(tmp_path, written[-1].id, reason="remove fixture disclosure")

    assert _bytes(tmp_path) == before
    assert snapshot.compact(tmp_path).duplicates == 1
    events.withdraw(tmp_path, written[-1].id, reason="remove fixture disclosure")
    assert PRIVATE not in b"".join(_bytes(tmp_path).values()).decode("utf-8")
    assert fsck.check(tmp_path).clean


def test_a_malformed_duplicate_cannot_retain_text_after_apparent_withdrawal(tmp_path: Path) -> None:
    written = events.append(
        tmp_path,
        [
            events.Draft(RECORD, events.KIND_CREATED, {"title": "safe"}),
            events.Draft(RECORD, events.KIND_COMMENT, {"text": PRIVATE}),
        ],
        writer="",
    )
    malformed = json.loads(events.to_json(written[-1]))
    malformed["seq"] = True
    events.pending_path(tmp_path, "malformed").write_text(
        json.dumps(malformed) + "\n", encoding="utf-8"
    )
    before = _bytes(tmp_path)

    with pytest.raises(events.InvalidEventError):
        events.withdraw(tmp_path, written[-1].id, reason="remove fixture disclosure")

    assert _bytes(tmp_path) == before

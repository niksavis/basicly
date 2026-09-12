from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basicly import artifact_record, tracker
from tests import flipped_tracker

KIND = "implementation-plan"
OTHER_KIND = "change-summary"
RECORD = "proj-feat"


def legacy_marker(kind: str, payload: dict) -> str:

    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{artifact_record.MARKER} kind={kind} {encoded}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:

    root = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [kit.events.Draft(RECORD, kit.events.KIND_STATUS, {"status": "open"})],
    )
    return root


def artifact_events(repo: Path, record: str) -> list[Any]:

    kind = tracker.kit(repo).events.KIND_ARTIFACT
    return [
        event
        for event in flipped_tracker.ledger_events(repo)
        if event.kind == kind and event.record == record
    ]


def record_marker(repo: Path, record: str, text: str) -> None:
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [kit.events.Draft(record, kit.events.KIND_COMMENT, {tracker.COMMENT_TEXT_KEY: text})],
    )


def test_a_rendered_marker_decodes_back_to_the_payload() -> None:
    payload = {"groups": [["feat.1"]], "tasks": [{"issue_id": "feat.1"}]}
    assert artifact_record.recorded_payload(legacy_marker(KIND, payload), KIND) == payload


def test_another_marker_family_is_not_an_artifact() -> None:
    assert artifact_record.recorded_payload("[harness-policy] checkpoint=decompose", KIND) is None


def test_the_other_kind_is_not_decoded_as_this_one() -> None:
    body = legacy_marker(OTHER_KIND, {"issue_id": "i"})
    assert artifact_record.recorded_payload(body, KIND) is None
    assert artifact_record.recorded_payload(body, OTHER_KIND) == {"issue_id": "i"}


def test_a_marker_with_no_kind_field_is_not_an_artifact() -> None:
    assert artifact_record.recorded_payload(f"{artifact_record.MARKER} {{}}", KIND) is None


def test_a_payload_that_is_not_json_decodes_to_the_raw_string() -> None:
    body = f"{artifact_record.MARKER} kind={KIND} {{not json"
    assert artifact_record.recorded_payload(body, KIND) == "{not json"


def test_artifact_absent_record_write_is_refused_naming_the_id(repo: Path) -> None:

    with pytest.raises(tracker.TrackerDivergenceError, match="proj-taepo"):
        artifact_record.write(repo, "proj-taepo", KIND, {"feature": "proj-taepo"})
    assert artifact_events(repo, "proj-taepo") == []


def test_a_write_is_readable_back_as_the_payload(repo: Path) -> None:
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})
    assert artifact_record.read(repo, RECORD, KIND) == {"feature": RECORD}


def test_the_event_carries_the_kind_as_a_field_and_the_body_under_its_own_key(
    repo: Path,
) -> None:

    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})

    payload = artifact_events(repo, RECORD)[0].payload
    assert payload[tracker.ARTIFACT_KIND_KEY] == KIND
    assert payload[tracker.ARTIFACT_BODY_KEY] == {"feature": RECORD}


def test_a_body_far_over_the_marker_cap_is_stored_byte_identical(repo: Path) -> None:

    payload = {
        "tasks": [{"issue_id": f"proj-feat.{index}", "why": "y" * 100} for index in range(160)]
    }
    assert len(json.dumps(payload).encode("utf-8")) > 20_000

    artifact_record.write(repo, RECORD, KIND, payload)

    stored = artifact_events(repo, RECORD)[0].payload
    assert artifact_record.read(repo, RECORD, KIND) == payload
    assert not [key for key in stored if key.endswith(("_truncated", "_original_length_bytes"))]


def test_writing_the_same_artifact_twice_records_one_event(repo: Path) -> None:
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})
    assert len(artifact_events(repo, RECORD)) == 1


def test_a_changed_payload_supersedes_the_one_it_is_recorded_beside(repo: Path) -> None:
    artifact_record.write(repo, RECORD, KIND, {"tasks": ["a"]})
    artifact_record.write(repo, RECORD, KIND, {"tasks": ["a", "b"]})
    assert len(artifact_events(repo, RECORD)) == 2
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["a", "b"]}


def test_the_same_payload_under_two_kinds_is_two_artifacts(repo: Path) -> None:
    artifact_record.write(repo, RECORD, KIND, {"issue_id": RECORD})
    artifact_record.write(repo, RECORD, OTHER_KIND, {"issue_id": RECORD})
    assert len(artifact_events(repo, RECORD)) == 2
    assert artifact_record.read(repo, RECORD, OTHER_KIND) == {"issue_id": RECORD}


def test_whatever_it_is_handed_is_recorded(repo: Path) -> None:
    artifact_record.write(repo, RECORD, KIND, {"not": "a valid plan"})
    assert artifact_record.read(repo, RECORD, KIND) == {"not": "a valid plan"}


def test_a_write_is_refused_inside_a_read_only_section(repo: Path) -> None:

    with (
        tracker.read_only("a pre-flight gate"),
        pytest.raises(tracker.TrackerWriteRefusedError) as caught,
    ):
        artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})

    assert "a pre-flight gate" in str(caught.value)
    assert artifact_events(repo, RECORD) == []


def test_a_unit_with_no_artifact_carries_none(repo: Path) -> None:
    assert artifact_record.read(repo, RECORD, KIND) is None


def test_a_legacy_marker_still_resolves_to_the_artifact_it_carries(repo: Path) -> None:
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["from a marker"]}))
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["from a marker"]}


def test_the_last_recorded_marker_wins(repo: Path) -> None:
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["superseded"]}))
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["current"]}))
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["current"]}


def test_a_marker_of_another_kind_does_not_answer_for_this_one(repo: Path) -> None:
    record_marker(repo, RECORD, legacy_marker(OTHER_KIND, {"issue_id": RECORD}))
    assert artifact_record.read(repo, RECORD, KIND) is None
    assert artifact_record.read(repo, RECORD, OTHER_KIND) == {"issue_id": RECORD}


def test_the_typed_event_answers_over_a_marker_for_the_same_kind(repo: Path) -> None:
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["cut"]}))
    artifact_record.write(repo, RECORD, KIND, {"tasks": ["re-recorded"]})
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["re-recorded"]}


def test_a_store_that_cannot_answer_raises_rather_than_reading_as_absent(
    tmp_path: Path,
) -> None:

    with pytest.raises(RuntimeError):
        artifact_record.read(tmp_path, RECORD, KIND)

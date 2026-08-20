"""The recorded form of a handoff artifact: recorded, found again, decoded.

Split out of `test_handoff.py` when the §9.4 naming gate was made binding
(basicly-u2hl.14), along the boundary the modules themselves state: *recorded form*
against *judgement*. Nothing here knows what an artifact must contain, so nothing here
loads a schema or asserts a schema refusal — those stay with `test_handoff.py`, and the
loop states that produce and consume artifacts stay with `test_handoff_states.py`.

**A real ledger throughout**, and that is the instrument the defect demands: the transport
used to cut the body at 4096 bytes, and a fake store that hands back whatever it was given
cannot show that a store does not cut. Only the one that owns the cap can. Bare `tmp_path`
is kept for the single case that needs a store which cannot answer at all.

`legacy_marker` renders the retired transport's line. It lives here rather than in
:mod:`basicly.artifact_record`, which no longer writes one; the contract and state suites
import it from here to seed the population still on disk.
"""

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
    """One artifact as the retired ``[harness-artifact]`` transport wrote it.

    A frozen wire format spelled as a literal on purpose: it renders what 44 rows already
    hold, so it may only change if those rows do.
    """
    encoded = json.dumps(payload, sort_keys=True, separators=(",", ":"))
    return f"{artifact_record.MARKER} kind={kind} {encoded}"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """A checkout with the kit installed and :data:`RECORD` open — the store that owns the cap.

    The record is opened rather than left implicit, the way `test_gate_source._repo` does
    and for the same reason: the artifact write refuses an id the ledger does not hold
    (`owned_write.refuse_a_write_to_an_absent_record`), so writing against one nothing
    created is a refusal now rather than the fixture shortcut it was.
    """
    root = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [kit.events.Draft(RECORD, kit.events.KIND_STATUS, {"status": "open"})],
    )
    return root


def artifact_events(repo: Path, record: str) -> list[Any]:
    """Every ``artifact`` event *record* carries in *repo*'s ledger, in file order.

    Exported because `test_handoff` asserts against the same population from the ruling's
    side, and two spellings of "what an artifact event is" could come to disagree.
    """
    kind = tracker.kit(repo).events.KIND_ARTIFACT
    return [
        event
        for event in flipped_tracker.ledger_events(repo)
        if event.kind == kind and event.record == record
    ]


def record_marker(repo: Path, record: str, text: str) -> None:
    """Put *text* on *record* the way the retired transport did — one ``comment`` event."""
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [kit.events.Draft(record, kit.events.KIND_COMMENT, {tracker.COMMENT_TEXT_KEY: text})],
    )


# --- decoding a marker: absent, other, and corrupted are three answers -------


def test_a_rendered_marker_decodes_back_to_the_payload() -> None:
    """The round trip the retired form has to keep passing, with no store in the way."""
    payload = {"groups": [["feat.1"]], "tasks": [{"issue_id": "feat.1"}]}
    assert artifact_record.recorded_payload(legacy_marker(KIND, payload), KIND) == payload


def test_another_marker_family_is_not_an_artifact() -> None:
    """Only this family's markers are read: a policy or run marker is not a handoff."""
    assert artifact_record.recorded_payload("[harness-policy] checkpoint=decompose", KIND) is None


def test_the_other_kind_is_not_decoded_as_this_one() -> None:
    """The kind is a field, so one family carries both without either answering for the other."""
    body = legacy_marker(OTHER_KIND, {"issue_id": "i"})
    assert artifact_record.recorded_payload(body, KIND) is None
    assert artifact_record.recorded_payload(body, OTHER_KIND) == {"issue_id": "i"}


def test_a_marker_with_no_kind_field_is_not_an_artifact() -> None:
    """An older or hand-written marker in this family, with no kind, carries nothing."""
    assert artifact_record.recorded_payload(f"{artifact_record.MARKER} {{}}", KIND) is None


def test_a_payload_that_is_not_json_decodes_to_the_raw_string() -> None:
    """*Corrupted* and *absent* are different answers, and only the first may refuse a state."""
    body = f"{artifact_record.MARKER} kind={KIND} {{not json"
    assert artifact_record.recorded_payload(body, KIND) == "{not json"


# --- writing: one typed event, and the body is not the cap's business -------


def test_artifact_absent_record_write_is_refused_naming_the_id(repo: Path) -> None:
    """The seventh write path: `add_artifact` bypasses `owned_write` and was unguarded.

    An artifact is how one loop state hands the next its evidence, so one filed against a
    mistyped id is evidence attached to nothing while the state that needed it reads as
    carrying none (basicly-kmqno2).
    """
    with pytest.raises(tracker.TrackerDivergenceError, match="proj-taepo"):
        artifact_record.write(repo, "proj-taepo", KIND, {"feature": "proj-taepo"})
    assert artifact_events(repo, "proj-taepo") == []


def test_a_write_is_readable_back_as_the_payload(repo: Path) -> None:
    """Record then read, through the store, is the seam's whole contract."""
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})
    assert artifact_record.read(repo, RECORD, KIND) == {"feature": RECORD}


def test_the_event_carries_the_kind_as_a_field_and_the_body_under_its_own_key(
    repo: Path,
) -> None:
    """The two payload keys, bound against the kit's own fold rather than against a fake.

    `events._apply_artifact` reads both as literals and exports no constant, so this is
    what fails if the kit renames either — instead of a body nothing reads.
    """
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})

    payload = artifact_events(repo, RECORD)[0].payload
    assert payload[tracker.ARTIFACT_KIND_KEY] == KIND
    assert payload[tracker.ARTIFACT_BODY_KEY] == {"feature": RECORD}


def test_a_body_far_over_the_marker_cap_is_stored_byte_identical(repo: Path) -> None:
    """The defect, and the size that reproduced it: 4096 bytes cut 31 of the first 54.

    Byte-identity is the assertion rather than "no exception": a cut body still reads back
    as a value, and it was the *difference* between what the producer validated and what
    the consumer got that refused 23 real record-and-kind pairs.
    """
    payload = {
        "tasks": [{"issue_id": f"proj-feat.{index}", "why": "y" * 100} for index in range(160)]
    }
    assert len(json.dumps(payload).encode("utf-8")) > 20_000

    artifact_record.write(repo, RECORD, KIND, payload)

    stored = artifact_events(repo, RECORD)[0].payload
    assert artifact_record.read(repo, RECORD, KIND) == payload
    assert not [key for key in stored if key.endswith(("_truncated", "_original_length_bytes"))]


def test_writing_the_same_artifact_twice_records_one_event(repo: Path) -> None:
    """A state re-entered on every advance must not bury its artifact under copies."""
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})
    artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})
    assert len(artifact_events(repo, RECORD)) == 1


def test_a_changed_payload_supersedes_the_one_it_is_recorded_beside(repo: Path) -> None:
    """Idempotence is on the whole body, so a genuinely new artifact still lands and wins."""
    artifact_record.write(repo, RECORD, KIND, {"tasks": ["a"]})
    artifact_record.write(repo, RECORD, KIND, {"tasks": ["a", "b"]})
    assert len(artifact_events(repo, RECORD)) == 2
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["a", "b"]}


def test_the_same_payload_under_two_kinds_is_two_artifacts(repo: Path) -> None:
    """The kind is a field of the event, so idempotence does not collapse the two."""
    artifact_record.write(repo, RECORD, KIND, {"issue_id": RECORD})
    artifact_record.write(repo, RECORD, OTHER_KIND, {"issue_id": RECORD})
    assert len(artifact_events(repo, RECORD)) == 2
    assert artifact_record.read(repo, RECORD, OTHER_KIND) == {"issue_id": RECORD}


def test_whatever_it_is_handed_is_recorded(repo: Path) -> None:
    """Validation is the caller's and happens first; this module rules on nothing."""
    artifact_record.write(repo, RECORD, KIND, {"not": "a valid plan"})
    assert artifact_record.read(repo, RECORD, KIND) == {"not": "a valid plan"}


def test_a_write_is_refused_inside_a_read_only_section(repo: Path) -> None:
    """The guard covers the write that states itself as a fact rather than as an argv.

    Here rather than in `test_tracker_seam.py` because this is the one caller of that
    seam: an artifact cannot be deleted from the append-only log either, so a pre-flight
    gate recording one is the same unrecoverable write the guard was built for.
    """
    with (
        tracker.read_only("a pre-flight gate"),
        pytest.raises(tracker.TrackerWriteRefusedError) as caught,
    ):
        artifact_record.write(repo, RECORD, KIND, {"feature": RECORD})

    assert "a pre-flight gate" in str(caught.value)
    assert artifact_events(repo, RECORD) == []


# --- reading: the event first, the marker still resolving --------------------


def test_a_unit_with_no_artifact_carries_none(repo: Path) -> None:
    """The honest empty answer, distinguished below from a store that could not answer."""
    assert artifact_record.read(repo, RECORD, KIND) is None


def test_a_legacy_marker_still_resolves_to_the_artifact_it_carries(repo: Path) -> None:
    """The population that cannot be re-recorded: 44 rows on a log nothing may rewrite."""
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["from a marker"]}))
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["from a marker"]}


def test_the_last_recorded_marker_wins(repo: Path) -> None:
    """Re-decomposed under a changed plan, a reader gets the plan that made the children."""
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["superseded"]}))
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["current"]}))
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["current"]}


def test_a_marker_of_another_kind_does_not_answer_for_this_one(repo: Path) -> None:
    """One family, two kinds, read apart — through the store as well as in the decoder."""
    record_marker(repo, RECORD, legacy_marker(OTHER_KIND, {"issue_id": RECORD}))
    assert artifact_record.read(repo, RECORD, KIND) is None
    assert artifact_record.read(repo, RECORD, OTHER_KIND) == {"issue_id": RECORD}


def test_the_typed_event_answers_over_a_marker_for_the_same_kind(repo: Path) -> None:
    """A re-record is the later fact, and the marker beside it may be the truncated body."""
    record_marker(repo, RECORD, legacy_marker(KIND, {"tasks": ["cut"]}))
    artifact_record.write(repo, RECORD, KIND, {"tasks": ["re-recorded"]})
    assert artifact_record.read(repo, RECORD, KIND) == {"tasks": ["re-recorded"]}


def test_a_store_that_cannot_answer_raises_rather_than_reading_as_absent(
    tmp_path: Path,
) -> None:
    """The hard seam, on purpose: "no artifact, carry on" is the fail-open shape here.

    ``tmp_path`` rather than the fixture: a checkout with no kit installed *is* a store
    that cannot answer, and no stub has to stand in for one.
    """
    with pytest.raises(RuntimeError):
        artifact_record.read(tmp_path, RECORD, KIND)

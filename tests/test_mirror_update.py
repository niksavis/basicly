from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from basicly import mirror, owned_store
from basicly.owned_store import TrackerDivergenceError

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def kit() -> Any:
    return owned_store.kit(REPO_ROOT)


def _one(drafts: list[Any]) -> Any:
    assert len(drafts) == 1, drafts
    return drafts[0]


def _by_kind(drafts: list[Any], kind: str) -> Any:
    matching = [draft for draft in drafts if draft.kind == kind]
    assert len(matching) == 1, (kind, drafts)
    return matching[0]


def test_a_status_move_and_a_field_edit_are_different_kinds(kit: Any) -> None:
    drafts = mirror.drafts(kit, ["update", "-s", "in_progress", "-t", "task", "b-1"], "")

    status = _by_kind(drafts, kit.events.KIND_STATUS)
    field = _by_kind(drafts, kit.events.KIND_FIELD)
    assert status.payload["status"] == "in_progress"
    assert (field.payload["name"], field.payload["value"]) == ("issue_type", "task")
    assert {status.record, field.record} == {"b-1"}


@pytest.mark.parametrize(
    ("flag", "name"),
    [
        ("--title", "title"),
        ("--description", "description"),
        ("-d", "description"),
        ("--body", "description"),
        ("--acceptance-criteria", "acceptance_criteria"),
        ("--acceptance", "acceptance_criteria"),
        ("--design", "design"),
        ("--notes", "notes"),
        ("--assignee", "assignee"),
        ("--owner", "owner"),
    ],
)
def test_a_filing_field_is_mirrored_under_the_key_brs_export_carries_it_under(
    kit: Any, flag: str, name: str
) -> None:

    text = "- a bullet"

    field = _one(mirror.drafts(kit, ["update", "b-1", flag, text], ""))

    assert field.kind == kit.events.KIND_FIELD
    assert (field.payload["name"], field.payload["value"]) == (name, text)


@pytest.mark.parametrize("spelling", ["3", "P3", "p3"])
def test_a_priority_is_mirrored_as_the_int_the_export_holds(kit: Any, spelling: str) -> None:

    field = _one(mirror.drafts(kit, ["update", "b-1", "-p", spelling], ""))

    assert field.payload["value"] == 3


def test_a_priority_that_is_neither_spelling_is_refused_as_a_divergence(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="neither a number nor a P-form"):
        mirror.drafts(kit, ["update", "b-1", "-p", "urgent"], "")


@pytest.mark.parametrize("flag", ["--add-label", "--remove-label"])
def test_an_accumulating_label_flag_is_refused_at_this_layer(kit: Any, flag: str) -> None:

    with pytest.raises(TrackerDivergenceError, match="resolves it"):
        mirror.drafts(kit, ["update", "b-1", f"{flag}=x"], "")


def test_a_resolved_label_set_is_stored_as_the_joined_form(kit: Any) -> None:

    (field,) = mirror.drafts(kit, ["update", "b-1", "--labels", "a,b"], "")

    payload = field.payload  # type: ignore[attr-defined]  — a kit Draft, typed as object
    assert payload["name"] == "labels"
    assert payload["value"] == "a,b"


def test_an_update_flag_with_no_equivalent_is_refused_not_dropped(kit: Any) -> None:

    with pytest.raises(TrackerDivergenceError, match=r"tracker_argv\.UPDATE_FIELD_FLAGS"):
        mirror.drafts(kit, ["update", "--estimate=30", "b-1"], "")


def test_an_update_naming_no_issue_is_refused(kit: Any) -> None:

    with pytest.raises(TrackerDivergenceError, match="names no record"):
        mirror.drafts(kit, ["update", "-s", "open"], "")


def test_a_multi_id_update_records_every_flag_on_every_id(kit: Any) -> None:
    drafts: list[Any] = mirror.drafts(kit, ["update", "b-1", "b-2", "-s", "in_progress"], "")

    assert [draft.record for draft in drafts] == ["b-1", "b-2"]
    assert {draft.payload["status"] for draft in drafts} == {"in_progress"}

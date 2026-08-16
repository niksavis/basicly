"""One `br update`, as the field and status events the owned ledger records it with.

Split out of `test_mirror.py` when `basicly-e2mz.30` widened the translatable flag set
from three to fifteen and took that module over the size cap. The boundary is
`mirror._update_drafts` against the other five translators, which keep their tests
there; nothing here reads a `create` echo, an edge or a gate row.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from basicly import mirror, owned_store
from basicly.owned_store import TrackerDivergenceError

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def kit() -> Any:
    """This repo's installed tracker kit, loaded once."""
    return owned_store.kit(REPO_ROOT)


def _one(drafts: list[Any]) -> Any:
    assert len(drafts) == 1, drafts
    return drafts[0]


def _by_kind(drafts: list[Any], kind: str) -> Any:
    matching = [draft for draft in drafts if draft.kind == kind]
    assert len(matching) == 1, (kind, drafts)
    return matching[0]


def test_a_status_move_and_a_field_edit_are_different_kinds(kit: Any) -> None:
    """`status` has its own event kind; everything else is a `field`."""
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
    """Each name is br's own export key, measured over the 892-record export 2026-08-16.

    Not a spelling chosen here: `br.owned_record` renders the folded fields straight back
    as the record, and `policy._has_acceptance_criteria` reads `acceptance_criteria` off
    it. The value leads with a dash because a filing field holds arbitrary prose.
    """
    text = "- a bullet"

    field = _one(mirror.drafts(kit, ["update", "b-1", flag, text], ""))

    assert field.kind == kit.events.KIND_FIELD
    assert (field.payload["name"], field.payload["value"]) == (name, text)


@pytest.mark.parametrize("spelling", ["3", "P3", "p3"])
def test_a_priority_is_mirrored_as_the_int_the_export_holds(kit: Any, spelling: str) -> None:
    """All three spellings reach br, which exports `3` for each (measured 2026-08-16).

    A stored `"P3"` would disagree with `create`, which has always written the int, and
    the differential compares status, readiness and gates only — so nothing would catch it.
    """
    field = _one(mirror.drafts(kit, ["update", "b-1", "-p", spelling], ""))

    assert field.payload["value"] == 3


def test_a_priority_that_is_neither_spelling_is_refused_as_a_divergence(kit: Any) -> None:
    """The conversion runs before br is spawned, so a bare `ValueError` would escape raw."""
    with pytest.raises(TrackerDivergenceError, match="neither a number nor a P-form"):
        mirror.drafts(kit, ["update", "b-1", "-p", "urgent"], "")


@pytest.mark.parametrize("flag", ["--add-label", "--remove-label", "--set-labels", "--labels"])
def test_a_label_flag_is_still_refused_because_br_accumulates_it(kit: Any, flag: str) -> None:
    """The ledger holds `labels`; what it cannot hold is a delta against the current set.

    Measured 2026-08-16: `--add-label c` appended to `['a','b']`, and even
    `--set-labels a --set-labels b` left both. One field event per occurrence would
    replace where br appended, so the refusal is the right answer rather than a gap.
    """
    with pytest.raises(TrackerDivergenceError, match=flag):
        mirror.drafts(kit, ["update", "b-1", f"{flag}=x"], "")


def test_an_update_flag_with_no_equivalent_is_refused_not_dropped(kit: Any) -> None:
    """Dropping it leaves the ledger missing precisely the field somebody just added.

    Spelled inline, and that is forced rather than stylistic: `VALUE_FLAGS["update"]`
    is built *from* the translatable flags, so a space-separated unknown flag leaves its
    value looking like a positional and the id guard fires first. Both refuse — this is
    the one that names the repair.
    """
    with pytest.raises(TrackerDivergenceError, match=r"br_argv\.UPDATE_FIELD_FLAGS"):
        mirror.drafts(kit, ["update", "--add-label=phase", "b-1"], "")


def test_an_update_naming_no_issue_is_refused(kit: Any) -> None:
    """A write about nothing is still refused, now that a write about many is not.

    This asserted that *two* ids are a refusal until `basicly-e2mz.24`, and that was the
    defect rather than the guard: `br update` takes many, so the mirror refused a write
    br had already made. Widening it to many must not widen it to none.
    """
    with pytest.raises(TrackerDivergenceError, match="names no issue"):
        mirror.drafts(kit, ["update", "-s", "open"], "")


def test_a_multi_id_update_records_every_flag_on_every_id(kit: Any) -> None:
    """`br update` takes many ids too, and the same refusal was one translator over."""
    drafts: list[Any] = mirror.drafts(kit, ["update", "b-1", "b-2", "-s", "in_progress"], "")

    assert [draft.record for draft in drafts] == ["b-1", "b-2"]
    assert {draft.payload["status"] for draft in drafts} == {"in_progress"}

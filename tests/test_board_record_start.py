from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

import pytest

from basicly import board_actions, board_asks, board_record, board_render, board_sections
from tests.test_board_asks import _page
from tests.test_board_record_page import QUIET, TEMPLATES, _verdict
from tests.test_board_wall import STAMPED, document


@pytest.fixture
def doc() -> dict[str, Any]:
    return document("wall-v1.json")


def test_the_start_offer_is_the_actions_own_argv_and_not_a_second_spelling() -> None:

    argv = board_actions.ACTIONS["record-start"].build({"issue": "basicly-x"})
    assert argv == ("loop", "run", "basicly-x", "--detach")
    assert "supervise" not in argv, "supervise fans out over children a ready leaf has none of"
    assert "--detach" in argv, "a synchronous run cannot finish inside the action timeout"
    assert board_actions.ACTIONS["record-start"].confirmed is False, (
        "starting a lane is spend the grant bounds, not a checkpoint the anti-autopilot rule guards"
    )


def test_a_start_is_offered_only_where_a_press_cannot_begin_a_second_lane() -> None:
    ready = {"id": QUIET, "ready": True}
    assert board_record.startable(ready, None) is True
    assert board_record.startable(ready, {"id": QUIET}) is False, "a lane already holds it"
    assert board_record.startable({"id": QUIET, "ready": False}, None) is False, "not ready"


def test_the_page_prints_the_start_command_only_when_the_caller_supplied_one(
    doc: dict[str, Any],
) -> None:
    filled = board_record.context(
        doc, _verdict(doc), QUIET, STAMPED, page=board_record.PageFacts(start_command="basicly go")
    )
    assert filled is not None
    unit = next(row for row in doc["units"] if row["id"] == QUIET)
    if unit.get("ready"):
        assert "basicly go" in board_render.render_record(filled, TEMPLATES)

    bare = board_record.context(doc, _verdict(doc), QUIET, STAMPED)
    assert bare is not None
    assert "start: " not in board_render.render_record(bare, TEMPLATES)


def _startable_ids(doc: dict[str, Any]) -> set[str]:
    lanes = {str(lane.get("id") or ""): lane for lane in doc.get("lanes") or []}
    return {
        str(unit["id"])
        for unit in doc.get("units") or []
        if unit.get("id") and board_record.startable(unit, lanes.get(str(unit["id"])))
    }


def test_the_offer_is_a_control_and_not_only_a_line_to_copy() -> None:

    page = _page([])
    armed = set(
        re.findall(r'value="record-start">\s*<input[^>]*name="issue" value="([\w.-]+)"', page)
    )
    assert re.search(r'value="record-(?:park|resume)"', page), (
        "no park or resume control on the page either, so this probe proves nothing"
    )
    assert armed, "the board registers record-start and draws no control that submits it"
    assert armed <= _startable_ids(document("wall-v1.json")), (
        "a start control was drawn on a record the predicate would not start"
    )


def test_the_start_control_carries_the_work_type_and_the_grant_root(doc: dict[str, Any]) -> None:

    forms = board_asks.starting(doc, "a-token")
    assert forms, "the wall fixture offers no start at all, so this proves nothing"
    ident = next(iter(forms))
    filled = {field["name"]: field["value"] for field in forms[ident]["fields"]}
    unit = next(row for row in doc["units"] if row["id"] == ident)
    assert filled["work_type"] == str(unit.get("type") or ""), "the work type did not prefill"
    assert forms[ident]["command"] == board_actions.start_command(
        board_record.start_form(doc, ident)
    ), "the command beside the button is not the one the button runs"


def test_a_board_with_no_server_draws_no_start_control(doc: dict[str, Any]) -> None:
    assert all(form["token"] == "" for form in board_asks.starting(doc, None).values())
    assert 'value="record-start"' not in _page([], token=None)


@dataclass(frozen=True)
class _FoldedState:
    record: str
    fields: dict[str, str]
    status: str = "open"


def test_a_record_owing_a_section_is_not_offered_a_start() -> None:

    unblocked = {"id": QUIET, "ready": True}
    assert board_record.startable(unblocked, None) is True, "the control: nothing owed"
    assert board_record.startable({**unblocked, "owes": ["## Trigger"]}, None) is False
    assert board_record.startable({**unblocked, "owes": []}, None) is True, "empty owes nothing"


def test_a_row_with_no_verdict_is_not_offered_a_start() -> None:
    assert board_record.startable({"id": QUIET, "ready": True, "owes": None}, None) is True, (
        "an absent verdict leaves the older two conditions deciding, as they did before"
    )
    assert board_record.startable({"id": QUIET, "ready": False, "owes": []}, None) is False


def test_a_unit_row_names_the_owed_sections_and_carries_no_body() -> None:

    body = "## Acceptance Criteria\n\n- given x then y\n" * 40

    state = _FoldedState(
        "basicly-x", {"title": "a record", "issue_type": "task", "description": body}
    )

    rows = board_sections.units([state], owes={"basicly-x": ("## Trigger",)})

    assert rows[0]["owes"] == ["## Trigger"]
    assert "description" not in rows[0]
    assert body[:40] not in repr(rows[0]), "the body must not reach the wire by any key"


def test_a_unit_row_left_out_of_the_verdict_is_left_unmarked() -> None:
    state = _FoldedState("basicly-y", {"title": "a record", "issue_type": "task"})

    assert "owes" not in board_sections.units([state], owes={})[0]
    assert "owes" not in board_sections.units([state])[0]

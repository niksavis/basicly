from __future__ import annotations

import re

from basicly import board_asks, board_regions, board_wall
from tests.test_board_asks import TOKEN, _page
from tests.test_board_wall import readings


def _units(*rows: dict[str, str]) -> board_wall.Readings:
    reads = board_wall.Readings(readings("wall-v1.json"))
    reads["units"] = board_wall.Reading(
        "units", board_wall.BY_KEY[board_wall.RENDERABLE], "", list(rows)
    )
    return reads


def test_the_verb_a_record_is_offered_is_chosen_by_its_status() -> None:
    forms = board_asks.parking(
        [
            {"id": "a", "status": "open"},
            {"id": "b", "status": "in_progress"},
            {"id": "c", "status": "deferred"},
            {"id": "d", "status": "closed"},
        ],
        TOKEN,
    )
    assert forms["a"]["action"] == board_asks.PARK
    assert forms["b"]["action"] == board_asks.PARK
    assert forms["c"]["action"] == board_asks.RESUME
    assert "d" not in forms, "the board has no verb that reopens a closed record"


def test_resume_writes_in_progress_and_never_open() -> None:

    (form,) = board_asks.parking([{"id": "a", "status": "deferred"}], TOKEN).values()
    assert "--status in_progress" in form["command"]
    assert "--status open" not in form["command"]


def test_no_record_is_offered_a_form_naming_an_empty_id() -> None:
    assert board_asks.parking(None, TOKEN) == {}
    assert board_asks.parking([{"status": "open"}, {"id": "", "status": "open"}], TOKEN) == {}


def test_a_parked_record_is_named_rather_than_only_counted() -> None:
    rows, dropped = board_regions.parked(
        _units(
            {"id": "basicly-p1", "status": "deferred", "title": "one", "priority": "P2"},
            {"id": "basicly-open", "status": "open", "title": "two", "priority": "P1"},
        )
    )
    assert [row["id"] for row in rows] == ["basicly-p1"], "only the parked ones"
    assert dropped == ""


def test_the_park_control_lands_on_the_ready_row_that_names_its_own_record() -> None:

    page = _page([])
    body = page[page.index('class="region ready') :]
    cell = r'<td class="id clip">(?:<a[^>]*>)?(basicly-[\w.]+)(?:</a>)?</td>.*?</tr>'
    rows = re.findall(cell, body, re.DOTALL)
    armed = re.findall(
        r'value="record-park">\s*<input type="hidden" name="issue" value="(basicly-[\w.]+)"',
        body,
    )
    assert rows, "the fixture draws no ready rows, so this proves nothing"
    assert len(armed) == len(rows), (
        f"{len(rows)} ready row(s) drew {len(armed)} park control(s); a zip over the two "
        "would have passed on the shorter list and proved nothing"
    )
    assert armed == rows, "a ready row is armed with another record's id"

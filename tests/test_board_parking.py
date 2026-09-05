"""Parking a record and bringing it back, and the row each verb is offered on.

The owner's words on 2026-09-05 were *"descope work, scope work"*. In this harness that is
`deferred` and back, and neither was on the board - nor was a deferred record itself. It is
out of the phase counts, out of the claimed rows and out of the ready set, so the only trace
one left was `4 parked, not counted at a phase`: a number with no member named, which is the
defect `basicly-5jkxqk` fixed one region over (basicly-arxhshr).

Two properties are worth more than the plumbing. The verb is chosen by status, so a board
never draws two buttons where one applies. And `resume` writes `in_progress` and never
`open`, because a record parked from `open` still holds that `open` and the write would be
skipped as a replay - `tracker_write` says so and exits 1.
"""

from __future__ import annotations

import re

from basicly import board_asks, board_regions, board_wall
from tests.test_board_asks import TOKEN, _page
from tests.test_board_wall import readings


def _units(*rows: dict[str, str]) -> board_wall.Readings:
    """Readings whose `units` section is exactly *rows*."""
    reads = board_wall.Readings(readings("wall-v1.json"))
    reads["units"] = board_wall.Reading(
        "units", board_wall.BY_KEY[board_wall.RENDERABLE], "", list(rows)
    )
    return reads


def test_the_verb_a_record_is_offered_is_chosen_by_its_status() -> None:
    """Two buttons where one applies is a question the board can answer itself."""
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
    """`open` is what a parked record was created with, so the write is dropped as a replay.

    `tracker_write` prints `already recorded, so nothing was appended` and returns 1, so a
    resume spelled that way is a button that reports failure and changes nothing.
    """
    (form,) = board_asks.parking([{"id": "a", "status": "deferred"}], TOKEN).values()
    assert "--status in_progress" in form["command"]
    assert "--status open" not in form["command"]


def test_no_record_is_offered_a_form_naming_an_empty_id() -> None:
    """A button armed with "" acts on whatever the CLI resolves that to."""
    assert board_asks.parking(None, TOKEN) == {}
    assert board_asks.parking([{"status": "open"}, {"id": "", "status": "open"}], TOKEN) == {}


def test_a_parked_record_is_named_rather_than_only_counted() -> None:
    """Nothing on the page drew one, so a person could park work and lose sight of it."""
    rows, dropped = board_regions.parked(
        _units(
            {"id": "basicly-p1", "status": "deferred", "title": "one", "priority": "P2"},
            {"id": "basicly-open", "status": "open", "title": "two", "priority": "P1"},
        )
    )
    assert [row["id"] for row in rows] == ["basicly-p1"], "only the parked ones"
    assert dropped == ""


def test_the_park_control_lands_on_the_ready_row_that_names_its_own_record() -> None:
    """A ready row is armed with its own record and no other.

    Keyed by id because the ready list is a bounded slice of `units[]`, so a positional join
    parks the wrong record - the one mistake a person cannot see happening.
    """
    page = _page([])
    body = page[page.index('class="region ready') :]
    rows = re.findall(r'<td class="id clip">(basicly-[\w.]+)</td>.*?</tr>', body, re.DOTALL)
    # Pinned to the park action, not to any `issue` input on the page. The first spelling
    # wanted `</form>` straight after the input; the submit button sits between, so it matched
    # nothing - and a `zip` over the two lists reported that as a pass.
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

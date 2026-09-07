"""The start offer on a record page: when it is made, and what it runs (basicly-fiow1sr).

Split out of `test_board_record_page` when that module crossed the size cap. The seam is the
offer rather than the page: these drive the action table, the predicate that decides whether a
press may begin a lane, and whether anything the board renders submits it.

The predecessor is why the argv is asserted against the table rather than against a literal.
It shipped `loop supervise <leaf> --max-passes 1 --detach`, which starts nothing, and its
tests passed because they checked the shape of a tuple.
"""

from __future__ import annotations

import re
from typing import Any

import pytest

from basicly import board_actions, board_asks, board_record, board_render
from tests.test_board_asks import _page
from tests.test_board_record_page import QUIET, TEMPLATES, _verdict
from tests.test_board_wall import STAMPED, document


@pytest.fixture
def doc() -> dict[str, Any]:
    """The wall fixture, parsed fresh so a mutating test cannot reach another one."""
    return document("wall-v1.json")


def test_the_start_offer_is_the_actions_own_argv_and_not_a_second_spelling() -> None:
    """The refused version shipped an argv that started nothing and its tests passed.

    They asserted the shape of a tuple. This asserts the command the page prints is produced
    by the table the button submits to, so the two cannot diverge - and that the verb is the
    detached one, because `loop supervise` on a leaf fans out over children it does not have
    and a bare `loop run` is synchronous against `board_actions.TIMEOUT_S` of 300 seconds
    (basicly-fiow1sr, basicly-zq9i2m.6).
    """
    argv = board_actions.ACTIONS["record-start"].build({"issue": "basicly-x"})
    assert argv == ("loop", "run", "basicly-x", "--detach")
    assert "supervise" not in argv, "supervise fans out over children a ready leaf has none of"
    assert "--detach" in argv, "a synchronous run cannot finish inside the action timeout"
    assert board_actions.ACTIONS["record-start"].confirmed is False, (
        "starting a lane is spend the grant bounds, not a checkpoint the anti-autopilot rule guards"
    )


def test_a_start_is_offered_only_where_a_press_cannot_begin_a_second_lane() -> None:
    """Ready and unheld, the acceptance's two conditions, driven through one function."""
    ready = {"id": QUIET, "ready": True}
    assert board_record.startable(ready, None) is True
    assert board_record.startable(ready, {"id": QUIET}) is False, "a lane already holds it"
    assert board_record.startable({"id": QUIET, "ready": False}, None) is False, "not ready"


def test_the_page_prints_the_start_command_only_when_the_caller_supplied_one(
    doc: dict[str, Any],
) -> None:
    """Against the rendered bytes: a context key the template never draws has passed before."""
    filled = board_record.context(
        doc, _verdict(doc), QUIET, STAMPED, page=board_record.PageFacts(start_command="basicly go")
    )
    assert filled is not None
    unit = next(row for row in doc["units"] if row["id"] == QUIET)
    if unit.get("ready"):
        assert "basicly go" in board_render.render_record(filled, TEMPLATES)

    # The control: no command supplied, nothing drawn, whatever the record's state.
    bare = board_record.context(doc, _verdict(doc), QUIET, STAMPED)
    assert bare is not None
    assert "start: " not in board_render.render_record(bare, TEMPLATES)


def _startable_ids(doc: dict[str, Any]) -> set[str]:
    """Every record in *doc* the predicate would offer a start on."""
    lanes = {str(lane.get("id") or ""): lane for lane in doc.get("lanes") or []}
    return {
        str(unit["id"])
        for unit in doc.get("units") or []
        if unit.get("id") and board_record.startable(unit, lanes.get(str(unit["id"])))
    }


def test_the_offer_is_a_control_and_not_only_a_line_to_copy() -> None:
    """A printed command satisfies every earlier test in this module and offers no press.

    That is how the first half shipped: `record-start` was registered on the POST endpoint
    while no template drew a form for it, so the acceptance's *offer* was a `<code>` block
    and the record's own demonstration - press start, watch a worktree appear - could not be
    performed. Park and resume are the positive control: the same probe finds them, so a
    zero here is the surface's and not the probe's (basicly-fiow1sr).
    """
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
    """Pressed without them the child reaches intake and stops - measured, not reasoned.

    The trap is that the two key spaces differ. `board_record.start_form` answers in field
    names and spells `work_type`; the field of that name is prefilled from the ask key
    `type`. Handed over unmapped the input draws empty and the button starts a lane that
    halts one step in.
    """
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
    """The `--out` artifact has nothing to post to; it keeps the line and loses the button."""
    assert all(form["token"] == "" for form in board_asks.starting(doc, None).values())
    assert 'value="record-start"' not in _page([], token=None)

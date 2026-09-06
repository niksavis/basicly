"""The start offer on a record page: when it is made, and what it runs (basicly-fiow1sr).

Split out of `test_board_record_page` when that module crossed the size cap. The seam is the
offer rather than the page: these three drive the action table and the predicate that decides
whether a press may begin a lane, which the rest of that module never touches.

The predecessor is why the argv is asserted against the table rather than against a literal.
It shipped `loop supervise <leaf> --max-passes 1 --detach`, which starts nothing, and its
tests passed because they checked the shape of a tuple.
"""

from __future__ import annotations

from typing import Any

import pytest

from basicly import board_actions, board_record, board_render
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

"""The nine questions an operator must be able to answer from the board and nothing else.

The owner set this test themselves, on 2026-09-05: *"if you as an agent look at the board,
and only have access to the board, would you be able to see what is going on? what work is
available, what work can start, is in progress? start work, stop work, inspect work (the
info written in the record in a structured way)? descope work, scope work?"*

They also said they had given that requirement **many times**. A requirement a person has to
repeat is a requirement with no gate behind it, and this is the gate (basicly-udunil8).

**Against the rendered page, never the model or the snapshot.** Both have passed while the
page drew nothing: `basicly-3qstvw` shipped an `asks` region whose producer never wrote
`actions`, and `basicly-yj8hpjr` is the standing record for that whole class. A question is
answerable when the *bytes a reader receives* answer it.

**A question nobody can answer yet is `xfail(strict=True)`, not `skip`.** The record asked
for a skip naming the open record; strict xfail is the same naming plus a ratchet, because
the day that record lands the gate turns red and somebody has to move the question into the
answered set. A skip would stay quiet forever and the count would never shrink.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from basicly import board_asks, board_render, board_schema, tracker
from tests.test_board_asks import TOKEN, _ask
from tests.test_board_render import TEMPLATES
from tests.test_board_wall import REPO_ROOT, document

# The questions no region answers yet, each against the open record that will answer it.
# Bound to the ledger below, so closing one of these without moving its question here is a
# failure rather than a quiet inconsistency.
UNANSWERED = {
    "what one record actually says": "basicly-62h3x9",
    "how to start a record": "basicly-fiow1sr",
    "when the work will be done": "basicly-hymq99",
}


def page() -> str:
    """The whole board as the server sends it, with every action surface assembled.

    A deferred unit is added because no shipped fixture carries one, and half of *descope*
    is the way back. The asks are the fixture's own.
    """
    doc: dict[str, Any] = document("wall-v1.json")
    now = datetime.now(UTC)
    doc["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    doc["asks"] = [_ask()]
    doc["units"] = [
        *doc["units"],
        {"id": "basicly-parked1", "status": "deferred", "title": "parked work", "priority": "P3"},
    ]
    rows, dropped = board_asks.pending(doc["asks"], TOKEN)
    filled = board_render.context(
        doc,
        board_schema.verdict(REPO_ROOT, doc),
        now,
        acts=(
            rows,
            dropped,
            board_asks.killable(doc.get("lanes"), TOKEN),
            board_asks.parking(doc.get("units"), TOKEN),
        ),
    )
    return board_render.render(filled, TEMPLATES)


def test_1_what_work_is_available() -> None:
    """A ranked list of records a reader can name, not a total."""
    drawn = page()
    assert "next up" in drawn.lower()
    assert re.search(r'<td class="id clip">basicly-[\w.]+</td>', drawn), "no record is named"


def test_2_what_can_start_now() -> None:
    """The set with nothing behind it, which is the difference between a backlog and a queue."""
    assert "needs nothing" in page(), "the queue does not separate ready from blocked"


def test_3_what_is_in_progress_and_who_holds_it() -> None:
    """A count with no member named is a count nobody can act on (basicly-5jkxqk)."""
    drawn = page()
    assert "running now" in drawn.lower()
    assert "branch" in drawn, "a running lane names no branch"
    assert "claimed, no lane" in drawn.lower(), "work nobody dispatched is unnamed"


def test_4_what_is_blocked_and_by_what() -> None:
    """Named blockers and the depth of the chain, not one `BLOCKED 56`."""
    drawn = page()
    assert "unblocks most" in drawn, "no blocker is named"
    assert "waits on a chain" in drawn, "the queue reports no depth"


@pytest.mark.xfail(strict=True, reason=f"open: {UNANSWERED['what one record actually says']}")
def test_5_what_one_record_actually_says() -> None:
    """The owner's *"the info written in the record in a structured way"*.

    `units[]` carries `id, phase, priority, ready, status, title, type` and no body, so a
    reader gets a title and stops. basicly-62h3x9 specifies the `/record/<id>` surface.
    """
    assert "/record/" in page(), "no record can be opened from the board"


@pytest.mark.xfail(strict=True, reason=f"open: {UNANSWERED['how to start a record']}")
def test_6_how_to_start_a_record() -> None:
    """No CLI verb starts one ready leaf detached, which is what blocks basicly-fiow1sr."""
    assert 'value="lane-start"' in page(), "no ready row offers to start"


def test_7_how_to_stop_a_lane() -> None:
    """`lane-kill` worked for a month with no surface to press it on (basicly-x1h1dl5)."""
    assert 'value="lane-kill"' in page(), "a running lane cannot be stopped from the board"


def test_8_how_to_descope_and_scope_a_record() -> None:
    """The owner's *"descope work, scope work"*: deferred, and the way back."""
    drawn = page()
    assert 'value="record-park"' in drawn, "no ready row can be parked"
    assert 'value="record-resume"' in drawn, "a parked record cannot be brought back"


@pytest.mark.xfail(strict=True, reason=f"open: {UNANSWERED['when the work will be done']}")
def test_9_when_the_work_will_be_done() -> None:
    """basicly-hymq99 draws a lane against its sized forecast and the runner timeout."""
    assert "forecast" in page(), "no lane says which bound will end it"


def test_every_unanswered_question_still_names_an_open_record() -> None:
    """The ratchet, and the reason the count can only shrink.

    Without this the map above is prose: a record could close, its question stay listed, and
    the board look worse than it is forever. Read through the engine rather than by grepping
    the log - a regex over ordered markers has reported the wrong answer here before.
    """
    for question, record in UNANSWERED.items():
        held = tracker.read_record(REPO_ROOT, record)
        assert held is not None, f"{record} is not in the ledger, so {question!r} names nothing"
        assert held.get("status") != "closed", (
            f"{record} closed, so {question!r} may be answerable now - move it out of "
            f"UNANSWERED and drop the xfail on its test, or say why it is still unanswered"
        )


def test_the_board_answers_six_of_the_nine_questions_today() -> None:
    """One number, so a reader of this file sees the score without counting tests."""
    asked = len([name for name in globals() if re.fullmatch(r"test_[1-9]_\w+", name)])
    assert asked == 9, "a question was added or lost without the count moving"
    assert len(UNANSWERED) == 3, (
        f"{9 - len(UNANSWERED)} of 9 answerable; update this figure in the same change "
        "that moves a question, so the score is never stale"
    )

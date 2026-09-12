from __future__ import annotations

import re
from datetime import UTC, datetime
from typing import Any

import pytest

from basicly import board_asks, board_render, board_schema, tracker
from tests.test_board_asks import TOKEN, _ask
from tests.test_board_record_page import page as record_page
from tests.test_board_render import TEMPLATES
from tests.test_board_wall import REPO_ROOT, document

UNANSWERED = {
    "when the work will be done": "basicly-hymq99",
}


def page() -> str:

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
            board_asks.starting(doc, TOKEN),
        ),
    )
    return board_render.render(filled, TEMPLATES)


def test_1_what_work_is_available() -> None:
    drawn = page()
    assert "next up" in drawn.lower()
    assert re.search(r'<td class="id clip">.*basicly-[\w.]+.*</td>', drawn), "no record is named"


def test_2_what_can_start_now() -> None:
    assert "needs nothing" in page(), "the queue does not separate ready from blocked"


def test_3_what_is_in_progress_and_who_holds_it() -> None:
    drawn = page()
    assert "running now" in drawn.lower()
    assert "branch" in drawn, "a running lane names no branch"
    assert "claimed, no lane" in drawn.lower(), "work nobody dispatched is unnamed"


def test_4_what_is_blocked_and_by_what() -> None:
    drawn = page()
    assert "unblocks most" in drawn, "no blocker is named"
    assert "waits on a chain" in drawn, "the queue reports no depth"


def test_5_what_one_record_actually_says() -> None:

    drawn = page()
    found = re.search(r'href="record/(basicly-[\w.]+)\.html"', drawn)
    assert found, "no record can be opened from the board"

    opened = record_page(document("wall-v1.json"), found[1])
    assert opened is not None, f"the board links to {found[1]}, which draws no page"
    for said in ("checkpoints", "rework", "blocked by", "the lane"):
        assert said in opened, f"the record page says nothing about {said!r}"


def test_6_how_to_start_a_record() -> None:

    drawn = page()
    assert 'value="record-park"' in drawn, "no control at all here, so this proves nothing"
    assert 'value="record-start"' in drawn, "no ready row offers to start"


def test_7_how_to_stop_a_lane() -> None:
    assert 'value="lane-kill"' in page(), "a running lane cannot be stopped from the board"


def test_8_how_to_descope_and_scope_a_record() -> None:
    drawn = page()
    assert 'value="record-park"' in drawn, "no ready row can be parked"
    assert 'value="record-resume"' in drawn, "a parked record cannot be brought back"


@pytest.mark.xfail(strict=True, reason=f"open: {UNANSWERED['when the work will be done']}")
def test_9_when_the_work_will_be_done() -> None:
    assert "forecast" in page(), "no lane says which bound will end it"


def test_every_unanswered_question_still_names_an_open_record() -> None:

    for question, record in UNANSWERED.items():
        held = tracker.read_record(REPO_ROOT, record)
        assert held is not None, f"{record} is not in the ledger, so {question!r} names nothing"
        assert held.get("status") != "closed", (
            f"{record} closed, so {question!r} may be answerable now - move it out of "
            f"UNANSWERED and drop the xfail on its test, or say why it is still unanswered"
        )


def test_the_board_answers_eight_of_the_nine_questions_today() -> None:
    asked = len([name for name in globals() if re.fullmatch(r"test_[1-9]_\w+", name)])
    assert asked == 9, "a question was added or lost without the count moving"
    assert len(UNANSWERED) == 1, (
        f"{9 - len(UNANSWERED)} of 9 answerable; update this figure in the same change "
        "that moves a question, so the score is never stale"
    )

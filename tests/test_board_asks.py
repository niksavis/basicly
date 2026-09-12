from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any

from basicly import (
    board_actions,
    board_asks,
    board_fields,
    board_render,
    board_schema,
    board_sections,
)
from tests.board_offline import assert_offline
from tests.test_board_wall import REPO_ROOT, document

if TYPE_CHECKING:
    from collections.abc import Sequence

TOKEN = "a-token"
TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"


def _ask(verb: str = "checkpoint-approve", **over: Any) -> dict[str, Any]:
    held = {
        "wait_id": "basicly-x#wait-ship",
        "issue": "basicly-x",
        "kind": "checkpoint",
        "subject": "ship",
        "question": "ship this?",
        "waiting_s": 90,
        "actions": [{"offer": f"do {verb}", "basicly": verb}],
    }
    return {**held, **over}


def _rows(asks: Sequence[dict[str, Any]], token: str | None = TOKEN) -> list[dict[str, Any]]:
    return list(board_asks.pending(asks, token)[0])


def _page(asks: Sequence[dict[str, Any]], token: str | None = TOKEN) -> str:
    doc = document("wall-v1.json")
    doc["asks"] = list(asks)
    now = datetime.now(UTC)
    doc["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, dropped = board_asks.pending(asks, token)
    filled = board_render.context(
        doc,
        board_schema.verdict(REPO_ROOT, doc),
        now,
        acts=(
            rows,
            dropped,
            board_asks.killable(doc.get("lanes"), token),
            board_asks.parking(doc.get("units"), token),
            board_asks.starting(doc, token),
        ),
    )
    return board_render.render(filled, TEMPLATES)


def _acts(page: str) -> str:

    mark = '<section class="region acts card">'
    return (
        page[page.index(mark) : page.index("</section>", page.index(mark))] if mark in page else ""
    )


def test_the_board_fills_in_what_it_already_knows_and_asks_only_for_the_rest() -> None:
    (row,) = _rows([_ask()])
    filled = {field["name"]: field["value"] for field in row["fields"]}
    assert filled == {"issue": "basicly-x", "name": "ship", "confirm": ""}
    typed = {field["name"] for field in row["fields"] if field["typed"]}
    assert typed == {"confirm"}, "the board is asking for something it already knows"


def test_each_verb_is_prefilled_from_the_key_that_actually_identifies_it() -> None:
    answer = _rows([_ask("loop-answer")])[0]
    assert {f["name"]: f["value"] for f in answer["fields"]} == {
        "decision_id": "basicly-x#wait-ship",
        "text": "",
    }
    kill = _rows([_ask("lane-kill")])[0]
    assert {f["name"]: f["value"] for f in kill["fields"]} == {
        "issue": "basicly-x",
        "reason": "",
        "confirm": "",
    }


def test_the_command_beside_the_button_is_the_argv_the_button_runs() -> None:
    (row,) = _rows([_ask()])
    assert row["command"] == (
        "basicly policy checkpoint basicly-x ship --approve --confirm <confirm code>"
    )
    assert row["command"].startswith("basicly ")
    assert "<confirm code>" in row["command"]
    assert _rows([_ask("loop-answer")])[0]["command"] == (
        "basicly loop answer -- basicly-x#wait-ship <the answer>"
    )


def test_an_offer_this_consumer_cannot_execute_yields_no_row(caplog: Any) -> None:

    assert board_actions.ACTIONS, "the positive control is empty, so this proves nothing"
    assert _rows([_ask("deploy-to-prod")]) == []
    assert _rows([{**_ask(), "actions": [{"offer": "have a look"}]}]) == []
    assert _rows([{**_ask(), "actions": "not a list"}]) == []
    assert _rows([{**_ask(), "actions": []}]) == []
    assert not caplog.records


def test_one_ask_offering_two_verbs_draws_one_row_each() -> None:
    both = _ask()
    both["actions"] = [
        {"offer": "approve", "basicly": "checkpoint-approve"},
        {"offer": "kill it", "basicly": "lane-kill"},
    ]
    assert [row["action"] for row in _rows([both])] == ["checkpoint-approve", "lane-kill"]
    assert [row["offer"] for row in _rows([both])] == ["approve", "kill it"]


def test_the_region_is_bounded_and_says_how_many_it_dropped() -> None:
    many = [_ask(issue=f"basicly-{n}") for n in range(board_asks.ASK_SLOTS + 4)]
    rows, dropped = board_asks.pending(many, TOKEN)
    assert len(rows) == board_asks.ASK_SLOTS
    assert dropped == 4
    assert board_asks.pending([_ask()], TOKEN)[1] == 0


def test_nothing_pending_draws_nothing_at_all() -> None:
    for empty in ([], None):
        assert board_asks.pending(empty, TOKEN) == ((), 0)
    page = _page([])
    assert _acts(page) == "", "the region is drawn with nothing pending"
    assert "needs a person" not in page


def test_a_board_with_no_server_still_names_the_line_to_type() -> None:

    (row,) = _rows([_ask()], token=None)
    assert row["token"] == ""
    assert row["command"].startswith("basicly policy checkpoint")
    page = _page([_ask()], token=None)
    assert "<form" not in page
    assert "basicly policy checkpoint basicly-x ship --approve" in page
    assert "run the line above" in page


def test_the_row_says_whether_a_one_time_code_is_owed() -> None:
    assert _rows([_ask("checkpoint-approve")])[0]["confirmed"] is True
    assert _rows([_ask("lane-kill")])[0]["confirmed"] is True
    assert _rows([_ask("loop-answer")])[0]["confirmed"] is False


def test_a_free_text_field_is_marked_so_it_can_be_drawn_wide() -> None:
    fields = {f["name"]: f["free"] for f in _rows([_ask("lane-kill")])[0]["fields"]}
    assert fields == {"issue": False, "reason": True, "confirm": False}


def test_the_confirm_input_carries_no_value_attribute_at_all() -> None:

    page = _page([_ask()])
    after = page.split('name="confirm"', 1)[1].split(">", 1)[0]
    assert "value=" not in after, f"the confirm input carries a value: {after!r}"
    known = page.split('name="issue"', 1)[1].split(">", 1)[0]
    assert 'value="basicly-x"' in known


def test_no_producer_string_reaches_the_page_unescaped() -> None:
    hostile = _ask(question='</form><img src=x onerror="alert(1)">', subject="a<b")
    page = _page([hostile])
    assert "<img" not in page
    assert 'onerror="' not in page
    assert "</form><img" not in page
    assert "&lt;img" in page
    assert "a&lt;b" in page
    assert "&lt;/form&gt;" in page


def test_the_region_is_drawn_inside_the_page_and_above_the_loop() -> None:

    page = _page([_ask()])
    assert page.index("<form") < page.index("</main>")
    assert page.index('class="region acts card"') < page.index('class="region loop')
    assert page.index('class="region band ') < page.index('class="region acts card"')
    assert '"acts"' in page, "the region has no grid area, so it is not in the layout"


def test_the_page_still_fetches_nothing_with_a_form_on_it() -> None:
    page = _page([_ask()])
    assert_offline(page)
    assert 'method="post"' in page


def test_the_rows_are_json_serialisable_so_the_seam_is_data_and_not_markup() -> None:
    rows = _rows([_ask()])
    assert json.loads(json.dumps(rows)) == rows
    flat = json.dumps(rows)
    assert not any(tag in flat for tag in ("<form", "<input", "<button", "<section"))


def _marker(kind: str, wait_id: str = "basicly-x#wait-ship") -> Any:
    return board_fields.Marker(
        family=board_fields.WAIT_FAMILY,
        record="basicly-x",
        at="2026-09-05T10:00:00Z",
        fields={"id": wait_id, "kind": kind},
        flags=frozenset(),
    )


def _produced(kind: str) -> dict[str, Any]:
    now = datetime(2026, 9, 5, 10, 5, tzinfo=UTC)
    (ask,) = board_sections.asks([_marker(kind)], now=now)
    return ask


def test_the_producer_names_a_verb_for_every_kind_the_engine_writes() -> None:
    assert _produced("checkpoint")["actions"] == [
        {"offer": "Approve it", "basicly": "checkpoint-approve"}
    ]
    assert _produced("decision")["actions"] == [{"offer": "Answer it", "basicly": "loop-answer"}]


def test_every_verb_the_producer_offers_is_one_this_consumer_can_run() -> None:

    offered = {verb for _label, verb in board_sections._OFFERS.values()}
    assert offered, "the positive control is empty, so this proves nothing"
    assert offered <= set(board_actions.ACTIONS), f"unrunnable verbs offered: {offered}"


def test_a_kind_the_table_does_not_name_gets_no_actions_key_at_all() -> None:
    produced = _produced("advance")
    assert "actions" not in produced
    assert board_asks.pending([produced], TOKEN) == ((), 0)
    assert produced["issue"] == "basicly-x" and produced["kind"] == "advance"


def test_a_real_produced_ask_reaches_the_page_as_a_prefilled_form() -> None:
    page = _page([_produced("checkpoint")])
    assert _acts(page).count("<form") == 1
    assert 'value="basicly-x"' in page and 'value="ship"' in page
    assert "basicly policy checkpoint basicly-x ship --approve --confirm" in page

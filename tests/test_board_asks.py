"""The actionable ask rows, asserted against the owner's sentence rather than the markup.

*"Checkpoints that wait on people should be immediately visible and actionable - that is why
we are building a board."* Three properties carry that, and each has a way of quietly failing:

* **prefilled.** The board already prints the id; a form that makes an operator retype it is
  a form they will leave and go to a terminal. What only a person holds - the answer, the
  reason, the confirm code - stays empty, and is *absent* rather than empty in the markup,
  because `value=""` and a missing attribute render the same and only one of them proves no
  code path could have filled it.
* **the command matches the button.** The line printed beside a control is built through the
  action's own argv, so it cannot name a different command from the one that runs.
* **inside the fold.** The panel this replaced was appended after `</main>` into a `100vh`
  body with `overflow: hidden`; 274px of it was clipped on a page with no scrollbar. A
  region that draws in the grid is the fix, and a page assertion is the only thing that
  holds it - no unit test can see a fold.
"""

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
from tests.test_board_wall import REPO_ROOT, document

if TYPE_CHECKING:
    from collections.abc import Sequence

TOKEN = "a-token"
TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"


def _ask(verb: str = "checkpoint-approve", **over: Any) -> dict[str, Any]:
    """One pending ask offering *verb*, with the keys a row is prefilled from."""
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
    """The rows *asks* produce, without the dropped count."""
    return list(board_asks.pending(asks, token)[0])


def _page(asks: Sequence[dict[str, Any]], token: str | None = TOKEN) -> str:
    """The whole board drawn with *asks* pending, the way the server draws it."""
    doc = document("wall-v1.json")
    doc["asks"] = list(asks)
    now = datetime.now(UTC)
    doc["generated_at"] = now.strftime("%Y-%m-%dT%H:%M:%SZ")
    rows, dropped = board_asks.pending(asks, token)
    # The kills too, exactly as `board_serve` assembles them: a helper that drew a narrower
    # page than the server does is how a region ships unfed (basicly-3qstvw).
    filled = board_render.context(
        doc,
        board_schema.verdict(REPO_ROOT, doc),
        now,
        acts=(rows, dropped, board_asks.killable(doc.get("lanes"), token)),
    )
    return board_render.render(filled, TEMPLATES)


def _acts(page: str) -> str:
    """Just the `needs a person` region of *page*, or "" where it was not drawn.

    Scoped rather than counting `<form` over the whole document: a lane card now carries a
    kill form of its own, and an assertion about this region that counts every form on the
    page fails on a change it was never about (basicly-x1h1dl5).
    """
    mark = '<section class="region acts">'
    return (
        page[page.index(mark) : page.index("</section>", page.index(mark))] if mark in page else ""
    )


def test_the_board_fills_in_what_it_already_knows_and_asks_only_for_the_rest() -> None:
    """An operator retyping an id the board is printing beside them is the defect."""
    (row,) = _rows([_ask()])
    filled = {field["name"]: field["value"] for field in row["fields"]}
    assert filled == {"issue": "basicly-x", "name": "ship", "confirm": ""}
    typed = {field["name"] for field in row["fields"] if field["typed"]}
    assert typed == {"confirm"}, "the board is asking for something it already knows"


def test_each_verb_is_prefilled_from_the_key_that_actually_identifies_it() -> None:
    """`loop answer` names the wait id; the other two name the issue. Not interchangeable."""
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
    """Built through `action.build`, so the two cannot drift into naming different commands."""
    (row,) = _rows([_ask()])
    assert row["command"] == (
        "basicly policy checkpoint basicly-x ship --approve --confirm <confirm code>"
    )
    assert row["command"].startswith("basicly ")
    # The placeholder is the field's own label, so what the operator must supply is named.
    assert "<confirm code>" in row["command"]
    assert _rows([_ask("loop-answer")])[0]["command"] == (
        "basicly loop answer -- basicly-x#wait-ship <the answer>"
    )


def test_an_offer_this_consumer_cannot_execute_yields_no_row(caplog: Any) -> None:
    """The schema's own rule: drawn without a button rather than refusing the document.

    Two shapes: a verb outside the closed enum, and an offer naming no verb at all - which is
    what a foreign producer's first honest attempt looks like.
    """
    assert board_actions.ACTIONS, "the positive control is empty, so this proves nothing"
    assert _rows([_ask("deploy-to-prod")]) == []
    assert _rows([{**_ask(), "actions": [{"offer": "have a look"}]}]) == []
    assert _rows([{**_ask(), "actions": "not a list"}]) == []
    assert _rows([{**_ask(), "actions": []}]) == []
    assert not caplog.records


def test_one_ask_offering_two_verbs_draws_one_row_each() -> None:
    """The offers are the producer's list, and a viewer may act on any it implements."""
    both = _ask()
    both["actions"] = [
        {"offer": "approve", "basicly": "checkpoint-approve"},
        {"offer": "kill it", "basicly": "lane-kill"},
    ]
    assert [row["action"] for row in _rows([both])] == ["checkpoint-approve", "lane-kill"]
    assert [row["offer"] for row in _rows([both])] == ["approve", "kill it"]


def test_the_region_is_bounded_and_says_how_many_it_dropped() -> None:
    """It takes height from the ready list's row; an unbounded one is the appended panel again."""
    many = [_ask(issue=f"basicly-{n}") for n in range(board_asks.ASK_SLOTS + 4)]
    rows, dropped = board_asks.pending(many, TOKEN)
    assert len(rows) == board_asks.ASK_SLOTS
    assert dropped == 4
    assert board_asks.pending([_ask()], TOKEN)[1] == 0


def test_nothing_pending_draws_nothing_at_all() -> None:
    """A standing form on a wall whose question is "does it need me?" answers it wrongly."""
    for empty in ([], None):
        assert board_asks.pending(empty, TOKEN) == ((), 0)
    page = _page([])
    assert _acts(page) == "", "the region is drawn with nothing pending"
    assert "needs a person" not in page


def test_a_board_with_no_server_still_names_the_line_to_type() -> None:
    """`--no-actions` and the static artifact: no token, so no form - but the command stands.

    The row is still built. An operator who cannot press a button is owed the line, and
    dropping the row would have left the ask visible with no way to answer it named.
    """
    (row,) = _rows([_ask()], token=None)
    assert row["token"] == ""
    assert row["command"].startswith("basicly policy checkpoint")
    page = _page([_ask()], token=None)
    assert "<form" not in page
    assert "basicly policy checkpoint basicly-x ship --approve" in page
    assert "run the line above" in page


def test_the_row_says_whether_a_one_time_code_is_owed() -> None:
    """Two of three actions need one. A note that is always there is a note nobody reads."""
    assert _rows([_ask("checkpoint-approve")])[0]["confirmed"] is True
    assert _rows([_ask("lane-kill")])[0]["confirmed"] is True
    assert _rows([_ask("loop-answer")])[0]["confirmed"] is False


def test_a_free_text_field_is_marked_so_it_can_be_drawn_wide() -> None:
    """The same flag the validator bounds by length instead of matching the id pattern."""
    fields = {f["name"]: f["free"] for f in _rows([_ask("lane-kill")])[0]["fields"]}
    assert fields == {"issue": False, "reason": True, "confirm": False}


def test_the_confirm_input_carries_no_value_attribute_at_all() -> None:
    """`value=""` and a missing attribute render identically; only absence proves the path.

    Over the drawn page, because this is a property of the markup and the markup moved to the
    template when the panel was retired.
    """
    page = _page([_ask()])
    after = page.split('name="confirm"', 1)[1].split(">", 1)[0]
    assert "value=" not in after, f"the confirm input carries a value: {after!r}"
    # The control: the field the board *does* know is drawn with its value, so a template
    # that dropped every `value` would fail here rather than passing the assertion above.
    known = page.split('name="issue"', 1)[1].split(">", 1)[0]
    assert 'value="basicly-x"' in known


def test_no_producer_string_reaches_the_page_unescaped() -> None:
    """An ask carries a producer's `question` and `subject`, and the page is HTML."""
    hostile = _ask(question='</form><img src=x onerror="alert(1)">', subject="a<b")
    page = _page([hostile])
    # The escaped text survives as inert characters, which is the point - so the assertion
    # is on the *syntax* that would execute, never on the words. `onerror` as a word is
    # harmless; `onerror="` is an attribute.
    assert "<img" not in page
    assert 'onerror="' not in page
    assert "</form><img" not in page
    assert "&lt;img" in page
    assert "a&lt;b" in page
    # The control: the page really did draw the hostile ask, so this is not a vacuous pass.
    assert "&lt;/form&gt;" in page


def test_the_region_is_drawn_inside_the_page_and_above_the_loop() -> None:
    """The whole defect: the panel this replaced sat 274px past a fold that never scrolls.

    Above the loop as well as inside it, because the layout is the acceptance criterion - a
    person being asked something reads before the workflow they are holding up.
    """
    page = _page([_ask()])
    assert page.index("<form") < page.index("</main>")
    assert page.index('class="region acts"') < page.index('class="region loop')
    assert page.index('class="region band ') < page.index('class="region acts"')
    assert '"acts"' in page, "the region has no grid area, so it is not in the layout"


def test_the_page_still_fetches_nothing_with_a_form_on_it() -> None:
    """The form is the one interactive element, and it is plain HTML with no runtime."""
    page = _page([_ask()])
    assert "<script" not in page
    assert "<link" not in page
    assert "src=" not in page
    assert 'method="post"' in page


def test_the_rows_are_json_serialisable_so_the_seam_is_data_and_not_markup() -> None:
    """A form assembled below this layer would have to be marked safe in the template."""
    rows = _rows([_ask()])
    assert json.loads(json.dumps(rows)) == rows
    # No markup was assembled anywhere below the template. Asserted on tags rather than on
    # `"<"`, because the command's placeholders are `<confirm code>` and are meant to be.
    flat = json.dumps(rows)
    assert not any(tag in flat for tag in ("<form", "<input", "<button", "<section"))


# --- the producer side of the same seam -------------------------------------
# Here rather than in `tests/test_board_sections.py`, and the reason is the defect: the
# consumer shipped reading `actions[].basicly` while the producer never wrote the key, and
# every test on either side passed. A seam asserted from one end only is how that happens
# (basicly-3qstvw). These assert the pair.


def _marker(kind: str, wait_id: str = "basicly-x#wait-ship") -> Any:
    """One pending wait marker of *kind*, the shape `board_fields.read_markers` yields."""
    return board_fields.Marker(
        family=board_fields.WAIT_FAMILY,
        record="basicly-x",
        at="2026-09-05T10:00:00Z",
        fields={"id": wait_id, "kind": kind},
        flags=frozenset(),
    )


def _produced(kind: str) -> dict[str, Any]:
    """What the producer writes for one pending ask of *kind*."""
    now = datetime(2026, 9, 5, 10, 5, tzinfo=UTC)
    (ask,) = board_sections.asks([_marker(kind)], now=now)
    return ask


def test_the_producer_names_a_verb_for_every_kind_the_engine_writes() -> None:
    """`checkpoint` and `decision` are the only two, from `policy` and `decisions`."""
    assert _produced("checkpoint")["actions"] == [
        {"offer": "Approve it", "basicly": "checkpoint-approve"}
    ]
    assert _produced("decision")["actions"] == [{"offer": "Answer it", "basicly": "loop-answer"}]


def test_every_verb_the_producer_offers_is_one_this_consumer_can_run() -> None:
    """The seam, asserted from the side that executes it.

    The schema's enum is the contract and neither module imports the other, so nothing but
    this pins them: a verb the producer invents would draw an ask with a button that refuses.
    """
    offered = {verb for _label, verb in board_sections._OFFERS.values()}
    assert offered, "the positive control is empty, so this proves nothing"
    assert offered <= set(board_actions.ACTIONS), f"unrunnable verbs offered: {offered}"


def test_a_kind_the_table_does_not_name_gets_no_actions_key_at_all() -> None:
    """Absent rather than empty, and never a default: a wrong verb is worse than none."""
    produced = _produced("advance")
    assert "actions" not in produced
    assert board_asks.pending([produced], TOKEN) == ((), 0)
    # The control: the ask itself is still well formed, so the band still draws it.
    assert produced["issue"] == "basicly-x" and produced["kind"] == "advance"


def test_a_real_produced_ask_reaches_the_page_as_a_prefilled_form() -> None:
    """End to end over the producer's own output, which is what nothing asserted before."""
    page = _page([_produced("checkpoint")])
    assert _acts(page).count("<form") == 1
    assert 'value="basicly-x"' in page and 'value="ship"' in page
    assert "basicly policy checkpoint basicly-x ship --approve --confirm" in page

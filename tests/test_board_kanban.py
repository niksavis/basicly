from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

from basicly import board_kanban, board_render, board_serve
from basicly.board_loop import PHASES
from tests.test_board_record_page import TEMPLATES, _verdict
from tests.test_board_serve import _get, _running, board_repo
from tests.test_board_wall import STAMPED, document

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["board_repo"]


@pytest.fixture
def doc() -> dict[str, Any]:
    return document("wall-v1.json")


def _unit(ident: str, **over: Any) -> dict[str, Any]:
    return {"id": ident, "phase": "intake", "status": "open", "title": ident, **over}


def test_every_phase_the_engine_declares_draws_a_column() -> None:

    drawn = board_kanban.columns({"units": []})
    assert tuple(col.name for col in drawn) == PHASES, "the columns are not the loop's own order"
    assert all(col.count == 0 for col in drawn), "an empty document filled a column"


def test_a_column_names_its_records_rather_than_only_counting_them() -> None:
    drawn = board_kanban.columns({
        "units": [_unit("basicly-a", title="one"), _unit("basicly-b", phase="build")]
    })
    by_name = {col.name: col for col in drawn}
    assert [card.title for card in by_name["intake"].cards] == ["one"]
    assert [card.ident for card in by_name["build"].cards] == ["basicly-b"]
    assert by_name["classify"].count == 0


def test_a_full_column_says_how_many_it_kept_back() -> None:
    units = [_unit(f"basicly-{n:02d}") for n in range(20)]
    col = next(c for c in board_kanban.columns({"units": units}, slots=5) if c.name == "intake")
    assert len(col.cards) == 5, "the cap did not hold"
    assert col.count == 20, "the header stopped counting at the cap"
    assert col.more == "+15 more"


def test_the_header_states_the_ratio_where_a_column_is_capped(doc: dict[str, Any]) -> None:

    drawn = board_render.render_kanban(
        board_kanban.context(doc, _verdict(doc), STAMPED, slots=2), TEMPLATES
    )
    capped = [col for col in board_kanban.columns(doc, slots=2) if col.more]
    assert capped, "no column capped at two slots, so this proves nothing"
    for col in capped:
        assert f"2 of {col.count}" in drawn, f"{col.name} states no ratio"


def test_a_record_an_agent_is_inside_is_marked_and_is_never_the_card_dropped() -> None:

    units = [_unit(f"basicly-{n:02d}", priority="P0") for n in range(9)]
    units.append(_unit("basicly-held", priority="P4"))
    drawn = board_kanban.columns(
        {"units": units, "lanes": [{"id": "basicly-held"}]},
        slots=1,
    )
    col = next(c for c in drawn if c.name == "intake")
    assert [card.ident for card in col.cards] == ["basicly-held"]
    assert col.cards[0].held is True, "the card does not say a worktree is inside it"


def test_a_parked_record_is_not_drawn_as_work_at_a_phase() -> None:
    drawn = board_kanban.columns({
        "units": [_unit("basicly-p", status="deferred"), _unit("basicly-o")]
    })
    col = next(c for c in drawn if c.name == "intake")
    assert [card.ident for card in col.cards] == ["basicly-o"]


def test_a_card_is_the_link_into_that_records_page(doc: dict[str, Any]) -> None:
    filled = board_kanban.context(doc, _verdict(doc), STAMPED)
    drawn = board_render.render_kanban(filled, TEMPLATES)
    hrefs = re.findall(r'<a class="card[^"]*" href="record/([\w.-]+)\.html"', drawn)
    assert hrefs, "no card on the page is a link"
    assert set(hrefs) <= set(filled["linkable"]), "a card links to a page nobody writes"


def test_the_title_leads_and_the_id_is_secondary(doc: dict[str, Any]) -> None:

    drawn = board_render.render_kanban(board_kanban.context(doc, _verdict(doc), STAMPED), TEMPLATES)
    blocks = drawn.split('class="card')
    assert len(blocks) > 1, "the fixture drew no card, so this proves nothing"
    first = blocks[1]
    assert first.index('class="title"') < first.index('class="ident"')


def test_an_unreadable_units_section_says_so_instead_of_drawing_seven_zeros(
    doc: dict[str, Any],
) -> None:
    doc["units"] = "not a list"
    filled = board_kanban.context(doc, _verdict(doc), STAMPED)
    assert filled["note"], "an unreadable section drew empty columns and no reason"
    assert all(col.count == 0 for col in filled["columns"])


def test_the_server_answers_the_loop_route_with_a_drawn_column_per_phase(
    board_repo: Path,
) -> None:

    with _running(board_serve.bind(board_repo, port=0)) as listener:
        status, body, _ = _get(f"http://{listener.host}:{listener.port}/loop")
    drawn = body.decode("utf-8")
    assert status == HTTPStatus.OK
    for phase in PHASES:
        assert f'class="name">{phase}<' in drawn, f"no column for {phase}"

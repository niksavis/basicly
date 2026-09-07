"""The loop as a column per phase: what each column holds, and what it keeps back.

The surface reverses `basicly-a68ggd` deliberately, and the assertions here are why that is a
reversal and not a regression. That finding was about a wall drowning in `intake`; the cap and
the overflow line keep its concern true while the members become visible (basicly-lc2bd3v.6).
"""

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
    """The wall fixture, parsed fresh so a mutating test cannot reach another one."""
    return document("wall-v1.json")


def _unit(ident: str, **over: Any) -> dict[str, Any]:
    """One unit row at `intake`, with the keys a card is built from."""
    return {"id": ident, "phase": "intake", "status": "open", "title": ident, **over}


def test_every_phase_the_engine_declares_draws_a_column() -> None:
    """A phase added to the engine cannot be invisible until something reaches it.

    Against `board_loop.PHASES` rather than a literal list: a second spelling of the loop's
    order is a second answer to what the loop is, and this one would go stale in silence.
    """
    drawn = board_kanban.columns({"units": []})
    assert tuple(col.name for col in drawn) == PHASES, "the columns are not the loop's own order"
    assert all(col.count == 0 for col in drawn), "an empty document filled a column"


def test_a_column_names_its_records_rather_than_only_counting_them() -> None:
    """The whole defect: seven boxes carrying a digit and nothing reachable inside one."""
    drawn = board_kanban.columns({
        "units": [_unit("basicly-a", title="one"), _unit("basicly-b", phase="build")]
    })
    by_name = {col.name: col for col in drawn}
    assert [card.title for card in by_name["intake"].cards] == ["one"]
    assert [card.ident for card in by_name["build"].cards] == ["basicly-b"]
    assert by_name["classify"].count == 0


def test_a_full_column_says_how_many_it_kept_back() -> None:
    """`intake` holds hundreds; a column that draws them all is the defect a68ggd closed."""
    units = [_unit(f"basicly-{n:02d}") for n in range(20)]
    col = next(c for c in board_kanban.columns({"units": units}, slots=5) if c.name == "intake")
    assert len(col.cards) == 5, "the cap did not hold"
    assert col.count == 20, "the header stopped counting at the cap"
    assert col.more == "+15 more"


def test_the_header_states_the_ratio_where_a_column_is_capped(doc: dict[str, Any]) -> None:
    """`272` over twelve cards is true about the phase and silent about the gap.

    Against the drawn header, because the `+N more` line sits under the last card and a tall
    column puts it below the fold - which is where a reader has already stopped looking.
    """
    drawn = board_render.render_kanban(
        board_kanban.context(doc, _verdict(doc), STAMPED, slots=2), TEMPLATES
    )
    capped = [col for col in board_kanban.columns(doc, slots=2) if col.more]
    assert capped, "no column capped at two slots, so this proves nothing"
    for col in capped:
        assert f"2 of {col.count}" in drawn, f"{col.name} states no ratio"


def test_a_record_an_agent_is_inside_is_marked_and_is_never_the_card_dropped() -> None:
    """A reader must be able to tell a phase a record is parked in from one being worked.

    Ordered first as well as marked: a held record that fell off the cap would be the one
    thing on the surface a reader most needs and cannot find.
    """
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
    """`working_phase`, so this surface and the diagram cannot disagree about `deferred`."""
    drawn = board_kanban.columns({
        "units": [_unit("basicly-p", status="deferred"), _unit("basicly-o")]
    })
    col = next(c for c in drawn if c.name == "intake")
    assert [card.ident for card in col.cards] == ["basicly-o"]


def test_a_card_is_the_link_into_that_records_page(doc: dict[str, Any]) -> None:
    """A phase is a way in, not a dead end - and the link is the card, not a token in it."""
    filled = board_kanban.context(doc, _verdict(doc), STAMPED)
    drawn = board_render.render_kanban(filled, TEMPLATES)
    hrefs = re.findall(r'<a class="card[^"]*" href="record/([\w.-]+)\.html"', drawn)
    assert hrefs, "no card on the page is a link"
    assert set(hrefs) <= set(filled["linkable"]), "a card links to a page nobody writes"


def test_the_title_leads_and_the_id_is_secondary(doc: dict[str, Any]) -> None:
    """The owner's ordering: *"IDs should be there but focus is on titles"*.

    Against the drawn bytes and by position, because a context key the template never draws
    has passed here before: the title's element must open before the id's inside one card.
    """
    drawn = board_render.render_kanban(board_kanban.context(doc, _verdict(doc), STAMPED), TEMPLATES)
    blocks = drawn.split('class="card')
    assert len(blocks) > 1, "the fixture drew no card, so this proves nothing"
    # To the next card, not to the next closing tag: the title closes its own span first, so
    # a non-greedy match ends before the id it is supposed to be compared against.
    first = blocks[1]
    assert first.index('class="title"') < first.index('class="ident"')


def test_an_unreadable_units_section_says_so_instead_of_drawing_seven_zeros(
    doc: dict[str, Any],
) -> None:
    """A phase holding nothing and a section this board could not read are not one fact."""
    doc["units"] = "not a list"
    filled = board_kanban.context(doc, _verdict(doc), STAMPED)
    assert filled["note"], "an unreadable section drew empty columns and no reason"
    assert all(col.count == 0 for col in filled["columns"])


def test_the_server_answers_the_loop_route_with_a_drawn_column_per_phase(
    board_repo: Path,
) -> None:
    """Against the live socket, not the renderer: a route can be unwired while both halves work.

    The columns rather than a 200, because a page that answers and draws nothing is the
    failure this surface exists to end - `basicly-3qstvw` shipped a region unfed that way.
    """
    with _running(board_serve.bind(board_repo, port=0)) as listener:
        status, body, _ = _get(f"http://{listener.host}:{listener.port}/loop")
    drawn = body.decode("utf-8")
    assert status == HTTPStatus.OK
    for phase in PHASES:
        assert f'class="name">{phase}<' in drawn, f"no column for {phase}"

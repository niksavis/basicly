from __future__ import annotations

import re
from http import HTTPStatus
from typing import TYPE_CHECKING, Any

import pytest

from basicly import board_backlog, board_render, board_serve
from tests.test_board_asks import _page as _wall
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
    return {"id": ident, "status": "open", "phase": "intake", "title": ident, **over}


def _doc(units: list[dict[str, Any]], edges: list[dict[str, str]] | None = None) -> dict[str, Any]:
    return {"units": units, "graph": {"edges": edges or []}}


def test_the_page_lists_every_record_units_carries_and_caps_nothing(doc: dict[str, Any]) -> None:

    drawn = board_render.render_backlog(
        board_backlog.context(doc, _verdict(doc), STAMPED), TEMPLATES
    )
    listed = set(re.findall(r'<td class="id">.*?>([\w.-]+)</a></td>', drawn))
    expected = {str(unit["id"]) for unit in doc["units"]}
    assert expected, "the fixture carries no units, so this proves nothing"
    assert listed == expected, f"{len(expected) - len(listed)} record(s) never reached the page"


def test_a_record_belonging_to_no_feature_is_named_rather_than_dropped() -> None:
    drawn = board_backlog.groups(
        _doc(
            [_unit("basicly-child"), _unit("basicly-parent"), _unit("basicly-loose")],
            [{"from": "basicly-child", "kind": "parent-child", "to": "basicly-parent"}],
        )
    )
    filed = {group.title: [row.ident for row in group.rows] for group in drawn}
    assert filed["basicly-parent"] == ["basicly-child"]
    assert sorted(filed[board_backlog.NO_FEATURE]) == ["basicly-loose", "basicly-parent"]


def test_the_unparented_bucket_sorts_last_however_much_of_it_is_ready() -> None:
    drawn = board_backlog.groups(
        _doc(
            [
                _unit("basicly-a", ready=True),
                _unit("basicly-b", ready=True),
                _unit("basicly-parent"),
                _unit("basicly-child"),
            ],
            [{"from": "basicly-child", "kind": "parent-child", "to": "basicly-parent"}],
        )
    )
    assert drawn[-1].title == board_backlog.NO_FEATURE


def test_a_row_says_what_holds_it_in_the_direction_the_producer_writes() -> None:

    drawn = board_backlog.groups(
        _doc(
            [_unit("basicly-waits"), _unit("basicly-holds")],
            [{"from": "basicly-waits", "kind": "blocks", "to": "basicly-holds"}],
        )
    )
    rows = {row.ident: row for group in drawn for row in group.rows}
    assert rows["basicly-waits"].blockers == ("basicly-holds",)
    assert rows["basicly-holds"].blockers == ()


def test_an_edge_onto_a_record_units_no_longer_lists_holds_nothing() -> None:
    drawn = board_backlog.groups(
        _doc(
            [_unit("basicly-waits")],
            [{"from": "basicly-waits", "kind": "blocks", "to": "basicly-gone"}],
        )
    )
    assert drawn[0].rows[0].blockers == ()


def test_the_header_counts_are_summed_over_the_rows_the_page_drew(doc: dict[str, Any]) -> None:
    filled = board_backlog.context(doc, _verdict(doc), STAMPED)
    rows = [row for group in filled["groups"] for row in group.rows]
    assert filled["totals"]["records"] == len(rows)
    assert filled["totals"]["ready"] == sum(1 for row in rows if row.ready)
    assert filled["totals"]["waiting"] == sum(1 for row in rows if row.blockers)
    assert (
        filled["totals"]["ready"] + filled["totals"]["blocked"] + filled["totals"]["parked"]
        == filled["totals"]["records"]
    ), "the three states do not sum to the record count a reader can check by eye"


def test_blocked_counts_the_population_the_wall_links_here_with() -> None:

    drawn = board_backlog.groups(
        _doc([
            _unit("basicly-ready", ready=True),
            _unit("basicly-stuck"),
            _unit("basicly-parked", status="deferred"),
        ])
    )
    counted = board_backlog.totals(drawn)
    assert counted["blocked"] == 1, "a parked record was counted as blocked"
    assert counted["parked"] == 1
    assert counted["ready"] + counted["blocked"] + counted["parked"] == counted["records"]


def test_not_ready_and_waiting_on_a_blocker_are_reported_as_two_figures() -> None:

    drawn = board_backlog.groups(
        _doc(
            [_unit("basicly-waits"), _unit("basicly-holds", ready=True), _unit("basicly-dor")],
            [{"from": "basicly-waits", "kind": "blocks", "to": "basicly-holds"}],
        )
    )
    counted = board_backlog.totals(drawn)
    assert counted["blocked"] == 2, "basicly-dor cannot be started and no edge says why"
    assert counted["waiting"] == 1, "only one record has a blocker this page can name"


def test_every_row_carries_the_fields_a_person_sorts_on(doc: dict[str, Any]) -> None:
    drawn = board_render.render_backlog(
        board_backlog.context(doc, _verdict(doc), STAMPED), TEMPLATES
    )
    unit = next(row for row in doc["units"] if row.get("priority") and row.get("phase"))
    block = drawn[drawn.index(f">{unit['id']}</a>") :][:600]
    for field in ("priority", "status", "phase"):
        assert str(unit[field]) in block, f"the row for {unit['id']} does not print its {field}"


def test_the_wall_makes_every_count_it_caps_the_way_in() -> None:

    drawn = _wall([])
    assert drawn.count(f'href="{board_render.BACKLOG_HREF}"') == 3, (
        "the ready overflow, the backlog strip and the queue are the three capped counts"
    )


def test_the_server_answers_the_backlog_route_with_every_record(board_repo: Path) -> None:
    with _running(board_serve.bind(board_repo, port=0)) as listener:
        status, body, _ = _get(f"http://{listener.host}:{listener.port}/backlog")
    drawn = body.decode("utf-8")
    assert status == HTTPStatus.OK
    assert "the backlog" in drawn
    assert re.search(r'<td class="id">', drawn), "the page answered and listed nothing"

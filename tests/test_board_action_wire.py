from __future__ import annotations

import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

from basicly import board_actions, board_serve
from tests.test_board_actions import PLANTED, TIMEOUT_S, _form, _post, _Spy, served

__all__ = ["served"]


def test_the_served_page_carries_the_pending_ask_prefilled_and_inside_the_layout(
    served: board_serve.Listener,
) -> None:
    surface = served.board.actions
    assert surface is not None
    page = urllib.request.urlopen(served.url + "/", timeout=TIMEOUT_S).read().decode("utf-8")

    assert page.count("<form") == 1
    assert page.count(f'value="{surface.token}"') == 1
    assert 'value="x-1"' in page and 'value="ship"' in page, "the form was not prefilled"
    assert "basicly policy checkpoint x-1 ship --approve --confirm" in page

    assert page.index("<form") < page.index("</main>")


def test_the_wire_refuses_an_empty_code_and_accepts_a_typed_one(
    served: board_serve.Listener,
) -> None:

    surface = served.board.actions
    assert surface is not None
    spy = _Spy()
    surface._run = spy
    route = f"{served.url}{board_actions.ROUTE}"

    empty = _form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm="")
    status, text = _post(route, empty, origin=served.url)
    assert status == HTTPStatus.BAD_REQUEST
    assert "confirm code is empty" in text
    assert spy.calls == []

    typed = _form(surface, "checkpoint-approve", issue="x-1", name="ship", confirm=PLANTED)
    status, text = _post(route, typed, origin=served.url)
    assert status == HTTPStatus.OK
    assert "APPROVED" in text
    assert PLANTED not in text
    assert spy.calls[0][-1] == PLANTED


def test_a_post_to_the_page_route_is_still_405_on_an_acting_board(
    served: board_serve.Listener,
) -> None:
    request = urllib.request.Request(served.url + "/", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=TIMEOUT_S)
    assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert refused.value.headers["Allow"] == "GET"

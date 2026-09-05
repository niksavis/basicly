"""The action route over the wire, on a board that is actually listening.

Split from `tests/test_board_actions.py` when the prefilled rows landed (basicly-ua9o5g),
along the seam that file already drew for itself: everything above the wire section asserts
:class:`basicly.board_actions.ActionSurface` in process - the closed table, the confirm-code
boundary, the origin and token refusals, the audit line. These assert a served board, which
is a different instrument and the only one that can answer whether a form is *reachable*.

The `assert page.index("<form") < page.index("</main>")` here is the load-bearing one. The
panel this replaced was appended after `</main>` into a `100vh` body with `overflow: hidden`,
and 274px of it was clipped on a page with no scrollbar - a defect no in-process assertion
could see, because the markup was all present and correct.
"""

from __future__ import annotations

import urllib.error
import urllib.request
from http import HTTPStatus

import pytest

from basicly import board_actions, board_serve
from tests.test_board_actions import PLANTED, TIMEOUT_S, _form, _post, _Spy, served

# Re-exported so pytest can resolve the fixture in this module. Imported rather than
# duplicated: two spellings of "a board that is listening" is two things to keep in step.
__all__ = ["served"]

# --- The wire: a served board, end to end -----------------------------------


def test_the_served_page_carries_the_pending_ask_prefilled_and_inside_the_layout(
    served: board_serve.Listener,
) -> None:
    """The record's criteria over the wire: within one fold, prefilled, command beside it."""
    surface = served.board.actions
    assert surface is not None
    page = urllib.request.urlopen(served.url + "/", timeout=TIMEOUT_S).read().decode("utf-8")

    # One form for the one offer, not one per table entry: the ask is what is actionable.
    assert page.count("<form") == 1
    assert page.count(f'value="{surface.token}"') == 1
    assert 'value="x-1"' in page and 'value="ship"' in page, "the form was not prefilled"
    assert "basicly policy checkpoint x-1 ship --approve --confirm" in page

    # Inside `</main>`, which is the whole defect this replaced: the body is 100vh with
    # `overflow: hidden`, so a form after it is drawn past a fold that never scrolls.
    assert page.index("<form") < page.index("</main>")


def test_the_wire_refuses_an_empty_code_and_accepts_a_typed_one(
    served: board_serve.Listener,
) -> None:
    """AC 4 and AC 6 through the socket, with the runner replaced so nothing is written.

    The accepted case asserts the code reached the argv and did not reach the reply, which is
    the pair the whole boundary rests on.
    """
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
    """Only the action route takes a POST; the page route says so, with an `Allow`."""
    request = urllib.request.Request(served.url + "/", data=b"", method="POST")
    with pytest.raises(urllib.error.HTTPError) as refused:
        urllib.request.urlopen(request, timeout=TIMEOUT_S)
    assert refused.value.code == HTTPStatus.METHOD_NOT_ALLOWED
    assert refused.value.headers["Allow"] == "GET"

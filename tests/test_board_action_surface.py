"""The endpoint half of the action seam: what it refuses before anything runs.

`board_action_surface` was split out of `board_actions` when that module crossed the size cap
(basicly-fiow1sr). The two answer different questions - that one *what verbs exist and what
argv each builds*, this one *how one is run and replied to* - and the seam is load-bearing:
three modules read the table and never serve anything, so a subprocess and an HTTP handler
have no business on their import path.

The suite driving a live listener stays in `test_board_action_wire`, and the table's own
contract stays in `test_board_actions`. What is here is the boundary between them: the
validator every posted form crosses, which is the one place untrusted input enters.
"""

from __future__ import annotations

from http import HTTPStatus

import pytest

from basicly import board_action_surface, board_actions


def _form(**fields: str) -> dict[str, list[str]]:
    """A posted form as the handler receives it: every value a list, as `parse_qs` returns."""
    return {name: [value] for name, value in fields.items()}


def test_the_split_left_the_table_and_the_endpoint_in_different_modules() -> None:
    """The seam itself, asserted so a later edit cannot quietly undo the split.

    `board_actions` must stay free of the endpoint: a module three consumers import to read a
    declaration should not carry a subprocess spawn or an HTTP status into their import graph.
    """
    assert not hasattr(board_actions, "ActionSurface")
    assert not hasattr(board_actions, "handle_post")
    assert hasattr(board_action_surface, "ActionSurface")
    # And the dependency runs one way: the endpoint reads the table, never the reverse.
    assert board_action_surface.ACTIONS is board_actions.ACTIONS


def test_a_required_field_left_empty_refuses_before_anything_is_spawned() -> None:
    """The refusal is the reply, with a status that says no - and nothing ran."""
    outcome = board_action_surface._validated(board_actions.ACTIONS["record-park"], _form(issue=""))
    assert isinstance(outcome, board_actions.Outcome)
    assert outcome.status == HTTPStatus.BAD_REQUEST
    assert "nothing was run" in outcome.text


def test_an_optional_field_left_empty_is_omitted_rather_than_refused() -> None:
    """basicly-fiow1sr: a record with no parent has no grant root.

    Refusing that submission would make an orphan record unstartable, so the field is declared
    optional and the builder omits the flag rather than passing an empty one. Both directions
    are driven, because a validator that accepted everything would pass the first half alone.
    """
    action = board_actions.ACTIONS["record-start"]
    values = board_action_surface._validated(
        action, _form(issue="basicly-x", work_type="", root="")
    )
    assert not isinstance(values, board_actions.Outcome), "an orphan record must remain startable"
    assert action.build(values) == ("loop", "run", "basicly-x", "--detach")

    supplied = board_action_surface._validated(
        action, _form(issue="basicly-x", work_type="bug", root="basicly-y")
    )
    assert not isinstance(supplied, board_actions.Outcome)
    assert action.build(supplied) == (
        "loop",
        "run",
        "basicly-x",
        "--detach",
        "--work-type",
        "bug",
        "--root",
        "basicly-y",
    )


@pytest.mark.parametrize("hostile", ["--flag", "a b", "a;b", "../x", "a\nb"])
def test_an_identifier_field_refuses_anything_that_is_not_one(hostile: str) -> None:
    """Every field arrives over HTTP from a screen anyone in the room can touch.

    The leading dash is the value an argv seam gets wrong, by handing argparse something it
    reads as a flag; the rest are the shapes a shell would have been the wrong boundary for.
    """
    outcome = board_action_surface._validated(
        board_actions.ACTIONS["record-park"], _form(issue=hostile)
    )
    assert isinstance(outcome, board_actions.Outcome)
    assert outcome.status == HTTPStatus.BAD_REQUEST


def test_the_confirm_code_is_redacted_out_of_anything_echoed() -> None:
    """A live credential must not reach the wall's screen or the server's stdout.

    The challenge `basicly` prints carries the code, so echoing an invocation verbatim would
    leave it where whoever walks past next can read it.
    """
    argv = ("basicly", "policy", "checkpoint", "x-1", "ship", "--confirm", "deadbeef")
    shown = board_action_surface.redacted(argv)
    assert "deadbeef" not in shown
    assert "--confirm" in shown, "the flag stays so a reader sees a code was passed"

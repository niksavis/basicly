from __future__ import annotations

from http import HTTPStatus

import pytest

from basicly import board_action_surface, board_actions


def _form(**fields: str) -> dict[str, list[str]]:
    return {name: [value] for name, value in fields.items()}


def test_the_split_left_the_table_and_the_endpoint_in_different_modules() -> None:

    assert not hasattr(board_actions, "ActionSurface")
    assert not hasattr(board_actions, "handle_post")
    assert hasattr(board_action_surface, "ActionSurface")
    assert board_action_surface.ACTIONS is board_actions.ACTIONS


def test_a_required_field_left_empty_refuses_before_anything_is_spawned() -> None:
    outcome = board_action_surface._validated(board_actions.ACTIONS["record-park"], _form(issue=""))
    assert isinstance(outcome, board_actions.Outcome)
    assert outcome.status == HTTPStatus.BAD_REQUEST
    assert "nothing was run" in outcome.text


def test_an_optional_field_left_empty_is_omitted_rather_than_refused() -> None:

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

    outcome = board_action_surface._validated(
        board_actions.ACTIONS["record-park"], _form(issue=hostile)
    )
    assert isinstance(outcome, board_actions.Outcome)
    assert outcome.status == HTTPStatus.BAD_REQUEST


def test_the_confirm_code_is_redacted_out_of_anything_echoed() -> None:

    argv = ("basicly", "policy", "checkpoint", "x-1", "ship", "--confirm", "deadbeef")
    shown = board_action_surface.redacted(argv)
    assert "deadbeef" not in shown
    assert "--confirm" in shown, "the flag stays so a reader sees a code was passed"

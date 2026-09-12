from __future__ import annotations

import pytest

from basicly import tracker_argv

UPDATE = tracker_argv.VALUE_FLAGS["update"]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["update", "b-1"], ["update", "b-1"]),
        (["update", "b-1", "b-2", "b-3"], ["update", "b-1", "b-2", "b-3"]),
        (["update", "-s", "open", "b-1"], ["update", "b-1"]),
        (["update", "-s=open", "b-1"], ["update", "b-1"]),
        (["close", "b-1", "--reason", "done"], ["close", "b-1"]),
    ],
)
def test_a_value_taking_flag_does_not_leave_its_value_looking_positional(
    args: list[str], expected: list[str]
) -> None:

    flags = tracker_argv.VALUE_FLAGS["close" if args[0] == "close" else "update"]

    assert tracker_argv.positionals(args, flags) == expected


def test_an_unknown_flags_value_stays_positional() -> None:

    assert tracker_argv.positionals(["update", "--estimate", "30", "b-1"], UPDATE) == [
        "update",
        "30",
        "b-1",
    ]


@pytest.mark.parametrize(
    ("args", "expected"),
    [
        (["update", "b-1", "-s", "open"], [("-s", "open")]),
        (["update", "b-1", "-s=open"], [("-s", "open")]),
        (["update", "b-1", "--type", "bug", "-s", "open"], [("--type", "bug"), ("-s", "open")]),
        (["update", "b-1", "-s"], [("-s", "")]),
        (["update", "b-1", "--flag="], [("--flag", "")]),
    ],
)
def test_both_flag_spellings_read_the_same_and_a_valueless_flag_is_kept(
    args: list[str], expected: list[tuple[str, str]]
) -> None:

    assert tracker_argv.flag_pairs(args, UPDATE) == expected


def test_the_value_flag_table_covers_every_translatable_update_flag() -> None:

    translatable = set(tracker_argv.UPDATE_FIELD_FLAGS) | tracker_argv.UPDATE_STATUS_FLAGS

    assert translatable <= UPDATE
    assert set(tracker_argv.CREATE_FIELD_FLAGS) <= tracker_argv.VALUE_FLAGS["create"]

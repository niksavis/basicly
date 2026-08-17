"""Reading a `br` write's argv: which words are positional, and which flags carry values.

Covered only through `mirror` until `basicly-e2mz.24` split the two, and indirectly is
the wrong place for it: the seam now asks these functions whether an argv is readable
*before* br is spawned, so a parsing edge that used to surface as a translation failure
now decides whether a command runs at all.
"""

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
    """The id a write is about is found by elimination, so a stray value becomes an id.

    `br gate report` puts the issue id last, after five flag pairs, so "the last word"
    is right only by accident and "every non-flag word" collects `--note`'s free text.
    """
    flags = tracker_argv.VALUE_FLAGS["close" if args[0] == "close" else "update"]

    assert tracker_argv.positionals(args, flags) == expected


def test_an_unknown_flags_value_stays_positional() -> None:
    """Deliberate: the space-separated unknown flag is what makes the mirror refuse.

    Absorbing it would make an untranslatable write look like an ordinary one, which is
    the silent-divergence direction. Refusing on a surprise id is the loud one.
    """
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
    """A flag with no value is reported as empty, never dropped.

    Dropping it would hand the translator a write it thinks it understood.
    """
    assert tracker_argv.flag_pairs(args, UPDATE) == expected


def test_the_value_flag_table_covers_every_translatable_update_flag() -> None:
    """The two tables are built from each other, and this is what pins that.

    A flag translatable but absent from `VALUE_FLAGS` would have its value read as an
    id, so the write would be refused for naming an issue that does not exist.
    """
    translatable = set(tracker_argv.UPDATE_FIELD_FLAGS) | tracker_argv.UPDATE_STATUS_FLAGS

    assert translatable <= UPDATE
    assert set(tracker_argv.CREATE_FIELD_FLAGS) <= tracker_argv.VALUE_FLAGS["create"]

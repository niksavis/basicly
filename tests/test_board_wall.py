from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from basicly import board_schema, board_wall

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board"

STAMPED = datetime(2026, 8, 21, 16, 42, 52, tzinfo=UTC)


def document(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def readings(name: str) -> dict[str, board_wall.Reading]:
    parsed = document(name)
    return board_wall.readings(parsed, board_schema.verdict(REPO_ROOT, parsed))


@pytest.mark.parametrize(
    ("part", "whole"),
    [(None, 100), (100, None), (100, 0), ("many", 100), (100, True), (True, 100)],
)
def test_a_bar_is_refused_when_either_term_is_absent_or_unmeasured(
    part: object, whole: object
) -> None:

    assert board_wall.bar(part, whole) is None


def test_a_bar_over_its_whole_says_so_rather_than_capping_silently() -> None:
    drawn = board_wall.bar(177_970_761, 4_000_000)
    assert drawn is not None
    assert (drawn.label, drawn.width, drawn.over) == ("4449%", 100.0, True)


def test_a_document_older_than_its_own_bound_reads_stale() -> None:
    fresh = board_wall.age(document("wall-v1.json"), STAMPED + timedelta(seconds=30))
    old = board_wall.age(document("wall-v1.json"), STAMPED + timedelta(seconds=90))
    assert fresh.state.key == board_wall.LIVE
    assert old.state.key == board_wall.STALE
    assert old.phrase == "1m 30s ago"
    assert old.stale_after == "60s", "the bound is the producer's, printed as given"


def test_an_undatable_document_is_stale_rather_than_blank() -> None:
    broken = {**document("minimal-v1.json"), "generated_at": "not a stamp"}
    drawn = board_wall.age(broken, STAMPED)
    assert drawn.state.key == board_wall.STALE
    assert drawn.phrase == "age unknown"


def test_every_state_is_encoded_on_a_glyph_and_a_border_as_well_as_colour() -> None:
    channels = {(state.glyph, state.border_style) for state in board_wall.STATES}
    assert len(channels) == len(board_wall.STATES), "two states share both non-colour channels"
    assert all(
        state.glyph.isprintable() and not state.glyph.isascii() for state in board_wall.STATES
    )


def test_the_alarm_colour_is_reserved_for_one_state() -> None:

    orange = [state.key for state in board_wall.STATES if state.colour == "var(--orange)"]
    assert orange == [board_wall.STUCK]


def test_every_section_the_verdict_named_gets_a_reading() -> None:
    parsed = document("wall-v1.json")
    verdict = board_schema.verdict(REPO_ROOT, parsed)
    reads = board_wall.readings(parsed, verdict)
    assert set(reads) == set(verdict.present) | set(verdict.absent)
    assert all(read.drawn for read in reads.values()), "the full fixture withholds nothing"


def test_a_section_the_producer_did_not_emit_reads_absent_rather_than_empty() -> None:
    reads = readings("no-phase-v1.json")
    absent = [read for read in reads.values() if read.state.key == board_wall.ABSENT]
    assert {read.name for read in absent} == {
        "session",
        "lanes",
        "asks",
        "spend",
        "health",
        "detail",
        "graph",
    }
    assert all(read.note == board_wall.ABSENT_TEXT for read in absent)
    assert all(read.held is None and not read.rows and not read.fields for read in absent)


def test_a_withheld_section_carries_the_violations_that_withheld_it() -> None:
    parsed = document("broken-section-v1.json")
    verdict = board_schema.verdict(REPO_ROOT, parsed)
    assert verdict.withheld, "the fixture no longer carries a non-conformant section"
    reads = board_wall.readings(parsed, verdict)
    for name in verdict.withheld:
        assert reads[name].state.key == board_wall.WITHHELD
        assert "$." in reads[name].note, "a withheld reading named no violation"


def test_a_clipped_value_carries_a_visible_marker() -> None:
    assert board_wall.clip("abcdef", 6) == "abcdef"
    assert board_wall.clip("abcdefg", 6) == "abcde\N{HORIZONTAL ELLIPSIS}"


def test_a_dropped_count_names_what_was_dropped() -> None:
    assert board_wall.more(3, "lanes") == "+3 more lanes"
    assert board_wall.more(0, "lanes") == ""
    assert board_wall.more(-2, "lanes") == ""


def test_a_number_the_producer_never_gave_reads_unmeasured() -> None:
    assert board_wall.number(None) == board_wall.UNKNOWN
    assert board_wall.number(0) == "0"
    assert board_wall.duration(None) == board_wall.UNKNOWN
    assert board_wall.duration(3661) == "1h 1m"


@pytest.mark.parametrize(
    ("seconds", "spelled"),
    [
        (536_280, "6 DAYS"),
        (86_400, "1 DAY"),
        (86_399, "23 HOURS"),
        (3600, "1 HOUR"),
        (3599, "59 MINUTES"),
        (60, "1 MINUTE"),
        (59, "59 SECONDS"),
        (1, "1 SECOND"),
        (0, "0 SECONDS"),
    ],
)
def test_a_headline_age_is_the_coarsest_unit_that_is_still_true(seconds: int, spelled: str) -> None:

    assert board_wall.coarse(seconds) == spelled


def test_an_undatable_stamp_falls_into_no_day_at_all() -> None:
    assert board_wall.day("2026-08-21T22:15:00Z") == "2026-08-21"
    assert board_wall.day("not a stamp") == ""
    assert board_wall.day(None) == ""


def test_a_row_is_filed_under_its_root_ancestor_and_not_its_immediate_parent() -> None:

    parents = {"task": "feature", "feature": "epic"}
    titles = {"epic": "the epic", "feature": "the feature", "task": "the task"}

    assert board_wall.feature_of("task", parents, titles) == "the epic"
    assert board_wall.feature_of("feature", parents, titles) == "the epic"
    assert board_wall.feature_of("epic", parents, titles) == ""


def test_a_cycle_terminates_and_reports_unattached_rather_than_a_member_of_the_loop() -> None:

    inside = {"a": "b", "b": "a"}
    assert board_wall.feature_of("a", inside, {"a": "A", "b": "B"}) == ""

    feeder = {"feeds": "a", "a": "b", "b": "a"}
    assert board_wall.feature_of("feeds", feeder, {"a": "A", "b": "B", "feeds": "F"}) == ""

    assert board_wall.feature_of("self", {"self": "self"}, {"self": "S"}) == ""


def test_a_chain_that_leaves_the_map_or_reaches_a_titleless_root_reads_unattached() -> None:

    assert board_wall.feature_of("task", {"task": "gone"}, {"task": "T"}) == ""
    assert board_wall.feature_of("task", {"task": "root"}, {"root": ""}) == ""

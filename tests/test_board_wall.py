"""The wall's vocabulary: the four honesty rules, each asserted against a refutation.

Not "a dataclass holds a value". Each of these is a rule the page would be dishonest without,
so each is attacked rather than demonstrated:

* **A bar needs both of its numbers.** The parametrised cases are the *refusals*, because a
  bar drawn against a term nobody measured reads as reassurance.
* **A value carries its age.** Both directions of the producer's own bound, and the
  undatable document, which must read stale rather than blank.
* **An absent section reads absent.** :func:`~basicly.board_wall.readings` is asserted to
  cover the verdict's whole roster, since a missing key is what a region would crash on and a
  fabricated one is what would read as a zero.
* **Three channels per state.** The pairwise assertion is the load-bearing one: eight states
  rendered with one glyph and one border style would satisfy "has a glyph" and tell a
  colour-blind reader nothing.
"""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from basicly import board_schema, board_wall

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board"

# The document's own instant, so an age is a function of the fixture and not of the clock.
STAMPED = datetime(2026, 8, 21, 16, 42, 52, tzinfo=UTC)


def document(name: str) -> dict[str, Any]:
    """One checked-in board fixture, parsed."""
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def readings(name: str) -> dict[str, board_wall.Reading]:
    """The section readings for a fixture, ruled on by the shipped schema."""
    parsed = document(name)
    return board_wall.readings(parsed, board_schema.verdict(REPO_ROOT, parsed))


@pytest.mark.parametrize(
    ("part", "whole"),
    [(None, 100), (100, None), (100, 0), ("many", 100), (100, True), (True, 100)],
)
def test_a_bar_is_refused_when_either_term_is_absent_or_unmeasured(
    part: object, whole: object
) -> None:
    """The raw number instead, and the refusal is the default rather than a special case.

    This repo has already shipped a wrong `context_window`; a bar drawn against a wrong
    ceiling reads as reassurance, which is worse than the number it replaced.
    """
    assert board_wall.bar(part, whole) is None


def test_a_bar_over_its_whole_says_so_rather_than_capping_silently() -> None:
    """The catastrophe signal: 4449% is the number an operator has to see."""
    drawn = board_wall.bar(177_970_761, 4_000_000)
    assert drawn is not None
    assert (drawn.label, drawn.width, drawn.over) == ("4449%", 100.0, True)


def test_a_document_older_than_its_own_bound_reads_stale() -> None:
    """`stale_after_s` is the producer's, and the page honours it in both directions."""
    fresh = board_wall.age(document("wall-v1.json"), STAMPED + timedelta(seconds=30))
    old = board_wall.age(document("wall-v1.json"), STAMPED + timedelta(seconds=90))
    assert fresh.state.key == board_wall.LIVE
    assert old.state.key == board_wall.STALE
    assert old.phrase == "1m 30s ago"
    assert old.stale_after == "60s", "the bound is the producer's, printed as given"


def test_an_undatable_document_is_stale_rather_than_blank() -> None:
    """A viewer that cannot date the file it draws has no grounds to call it live."""
    broken = {**document("minimal-v1.json"), "generated_at": "not a stamp"}
    drawn = board_wall.age(broken, STAMPED)
    assert drawn.state.key == board_wall.STALE
    assert drawn.phrase == "age unknown"


def test_every_state_is_encoded_on_a_glyph_and_a_border_as_well_as_colour() -> None:
    """Three channels, and the two non-colour ones must actually discriminate."""
    channels = {(state.glyph, state.border_style) for state in board_wall.STATES}
    assert len(channels) == len(board_wall.STATES), "two states share both non-colour channels"
    assert all(
        state.glyph.isprintable() and not state.glyph.isascii() for state in board_wall.STATES
    )


def test_the_alarm_colour_is_reserved_for_one_state() -> None:
    """`site/index.html` ships no red, so orange is the alarm and only the band may use it."""
    orange = [state.key for state in board_wall.STATES if state.colour == "var(--orange)"]
    assert orange == [board_wall.WAITING]


def test_every_section_the_verdict_named_gets_a_reading() -> None:
    """A region indexes a section by name, so a missing key is a crash and a made-up one a lie."""
    parsed = document("wall-v1.json")
    verdict = board_schema.verdict(REPO_ROOT, parsed)
    reads = board_wall.readings(parsed, verdict)
    assert set(reads) == set(verdict.present) | set(verdict.absent)
    assert all(read.drawn for read in reads.values()), "the full fixture withholds nothing"


def test_a_section_the_producer_did_not_emit_reads_absent_rather_than_empty() -> None:
    """The foreign case: six sections absent, each saying it was never measured."""
    reads = readings("no-phase-v1.json")
    absent = [read for read in reads.values() if read.state.key == board_wall.ABSENT]
    assert {read.name for read in absent} == {
        "session",
        "lanes",
        "asks",
        "spend",
        "health",
        "graph",
    }
    assert all(read.note == board_wall.ABSENT_TEXT for read in absent)
    assert all(read.held is None and not read.rows and not read.fields for read in absent)


def test_a_withheld_section_carries_the_violations_that_withheld_it() -> None:
    """A panel reporting non-conformance without saying why sends the producer the whole file."""
    parsed = document("broken-section-v1.json")
    verdict = board_schema.verdict(REPO_ROOT, parsed)
    assert verdict.withheld, "the fixture no longer carries a non-conformant section"
    reads = board_wall.readings(parsed, verdict)
    for name in verdict.withheld:
        assert reads[name].state.key == board_wall.WITHHELD
        assert "$." in reads[name].note, "a withheld reading named no violation"


def test_a_clipped_value_carries_a_visible_marker() -> None:
    """Truncation is the model's, not CSS overflow's: a cut with no marker is a silent one."""
    assert board_wall.clip("abcdef", 6) == "abcdef"
    assert board_wall.clip("abcdefg", 6) == "abcde\N{HORIZONTAL ELLIPSIS}"


def test_a_dropped_count_names_what_was_dropped() -> None:
    """`+N more <noun>`, and nothing at all when nothing was dropped."""
    assert board_wall.more(3, "lanes") == "+3 more lanes"
    assert board_wall.more(0, "lanes") == ""
    assert board_wall.more(-2, "lanes") == ""


def test_a_number_the_producer_never_gave_reads_unmeasured() -> None:
    """`not measured` rather than 0, which is the whole zero-versus-absent rule one field down."""
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
    """`6 DAYS`, not `148h 52m`, and truncating rather than rounding is what keeps it honest.

    Every boundary in both directions, because the failure this guards is a wall reading one
    unit too coarse - `86399` seconds rounded up is a day that has not happened.
    """
    assert board_wall.coarse(seconds) == spelled


def test_an_undatable_stamp_falls_into_no_day_at_all() -> None:
    """The throughput figure keys on this, so a row nobody could date must not land in today."""
    assert board_wall.day("2026-08-21T22:15:00Z") == "2026-08-21"
    assert board_wall.day("not a stamp") == ""
    assert board_wall.day(None) == ""

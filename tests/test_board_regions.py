"""The four question regions, asserted against the four questions rather than their markup.

The owner's verdict on the render this replaces was that the page did not answer *what is
being implemented, where the loop is, what state it is in, or what is in the backlog*. So each
region is asserted on the answer it owes:

* **the alarm** must say whether a person is waiting, in all five of its spellings, must lead
  with the *age* in the coarsest unit that is still true, and must never report an unreadable
  `asks` section as a quiet room;
* **the loop** must count per phase, carry each count's share of the population as a bar, and
  mark where work sits - and when no unit carries a phase, must say so rather than draw seven
  noughts;
* **running now** must draw one card per lane and *nothing* when there is no lane, because a
  reserved frame announcing nothing is the dead space this layout was redrawn to remove;
* **next up** must rank the ready set in two shapes - the narrow column beside the lanes, and
  the reclaimed width where its top rows must not be truncated at all - and must distinguish
  three different absences from an empty one.
"""

from __future__ import annotations

from datetime import timedelta

from basicly import board_regions, board_wall
from tests.test_board_wall import STAMPED, document, readings


def _reads(name: str, **override: object) -> dict[str, board_wall.Reading]:
    """A fixture's readings, with named sections replaced by hand-built ones."""
    reads = readings(name)
    for key, value in override.items():
        reads[key] = board_wall.Reading(key, board_wall.BY_KEY[board_wall.RENDERABLE], "", value)
    return reads


def _age(name: str, after_s: float = 8.0) -> board_wall.Age:
    """The fixture's own age, taken against an injected instant."""
    return board_wall.age(document(name), STAMPED + timedelta(seconds=after_s))


def _swap(
    name: str, reads: dict[str, board_wall.Reading], state: str, note: str
) -> dict[str, board_wall.Reading]:
    """*reads* with *name* replaced by a reading in *state*, carrying *note* and no value."""
    return {**reads, name: board_wall.Reading(name, board_wall.BY_KEY[state], note)}


def _absent(name: str, reads: dict[str, board_wall.Reading]) -> dict[str, board_wall.Reading]:
    """*reads* with *name* replaced by the reading a section the producer omitted gets."""
    return _swap(name, reads, board_wall.ABSENT, board_wall.ABSENT_TEXT)


def test_a_pending_ask_leads_with_its_age_in_the_coarsest_unit_that_is_still_true() -> None:
    """The one question a display in a room exists to move, and the age is the headline.

    `31 MINUTES`, not `31m 2s`: the criterion is that the largest text in the region is the
    age, so the count of asks steps down to the kicker and the id and kind go beneath. The
    exact phrase stays on that detail line, which is the assertion that this is a *ranking*
    rather than a loss.
    """
    band = board_regions.band(_reads("wall-v1.json"), _age("wall-v1.json"), STAMPED)
    assert band.state.key == board_wall.WAITING
    assert band.headline == "31 MINUTES"
    assert band.kicker == "3 waiting on a person"
    assert "basicly-4t9z" in band.lines[0], "the longest wait is not first"
    assert "31m 2s" in band.lines[0], "the exact figure left the page with the coarse one"
    assert band.lines[-1] == "+2 more waiting", "an ask was dropped with no marker"
    assert not band.stale


def test_an_empty_ask_list_reads_calm_and_an_unreadable_one_never_does() -> None:
    """A section that could not be read must not be reported as a quiet room."""
    calm = board_regions.band(_reads("wall-v1.json", asks=[]), _age("wall-v1.json"), STAMPED)
    assert calm.state.key == board_wall.CALM
    assert calm.headline == "NOTHING IS WAITING"
    assert not calm.kicker, "a quiet room costs one token and no count"

    reads = readings("no-phase-v1.json")
    absent = board_regions.band(reads, _age("no-phase-v1.json"), STAMPED)
    assert absent.state.key == board_wall.ABSENT
    assert absent.headline == "ASKS NOT EMITTED"
    assert absent.lines == (board_wall.ABSENT_TEXT,)


def test_a_stale_document_says_so_without_hiding_what_it_last_knew() -> None:
    """The marker is appended to whichever ask verdict holds, never in place of it."""
    band = board_regions.band(_reads("wall-v1.json"), _age("wall-v1.json", 900), STAMPED)
    assert band.state.key == board_wall.WAITING, "the stale marker replaced the ask verdict"
    assert band.stale.startswith("STALE")
    assert "bound 60s" in band.stale
    assert band.headline == "31 MINUTES"
    assert band.lines[0], "a stale board drew no last known value"


def test_the_band_reads_five_ways_and_no_more() -> None:
    """Each spelling exercised, rather than a list of names read back off the module.

    A declared list agrees with itself while the branch that would produce a sixth reading
    goes unasserted, so the band is driven into all five instead: the four exclusive ask
    verdicts, and the stale marker that rides on whichever of them holds.
    """
    reads, fresh = _reads("wall-v1.json"), _age("wall-v1.json")
    withheld = _swap("asks", reads, board_wall.WITHHELD, "$.asks[0]: too long")
    verdicts = {
        board_regions.band(reads, fresh, STAMPED).state.key: "waiting",
        board_regions.band(_reads("wall-v1.json", asks=[]), fresh, STAMPED).state.key: "calm",
        board_regions.band(_absent("asks", reads), fresh, STAMPED).state.key: "absent",
        board_regions.band(withheld, fresh, STAMPED).state.key: "withheld",
    }
    assert set(verdicts) == {
        board_wall.WAITING,
        board_wall.CALM,
        board_wall.ABSENT,
        board_wall.WITHHELD,
    }
    assert board_regions.band(withheld, fresh, STAMPED).headline == "ASKS WITHHELD"
    assert "$.asks[0]" in board_regions.band(withheld, fresh, STAMPED).lines[0]
    assert board_regions.band(reads, _age("wall-v1.json", 900), STAMPED).stale.startswith("STALE")
    assert not board_regions.band(reads, fresh, STAMPED).stale


def test_the_loop_counts_a_phase_per_unit_and_marks_where_the_lanes_are() -> None:
    """`units.phase` is where a unit stopped; `lanes.phase` is where one is running."""
    phases, note = board_regions.loop(_reads("wall-v1.json"))
    assert [phase.name for phase in phases] == list(board_regions.PHASES)
    assert [phase.count for phase in phases] == [4, 2, 3, 5, 2, 1, 2]
    assert {phase.name for phase in phases if phase.here} == {"build", "verify", "ship"}
    assert not note


def test_every_phase_carries_its_share_of_the_phased_population_as_a_bar() -> None:
    """Seven counts in seven identical boxes rank by nothing; the bar makes 213 against 1 read.

    The share is of the *phased* population and not the units section's length: an unphased
    unit is in no phase, so counting it would make every bar short of its row by the same wrong
    amount. Driven with a population the digits cannot rank at six metres.
    """
    lopsided = [{"id": f"u-{n}", "phase": "intake"} for n in range(213)]
    lopsided.append({"id": "u-late", "phase": "ship"})
    lopsided.append({"id": "u-none"})
    phases, _ = board_regions.loop(_reads("wall-v1.json", units=lopsided))
    shares = {phase.name: phase.share for phase in phases}
    assert shares["intake"] is not None and shares["intake"].label == "100%"
    assert shares["ship"] is not None and shares["ship"].label == "0%"
    assert shares["intake"].width > shares["ship"].width * 50, "the bar does not rank the counts"
    assert shares["build"] is not None and shares["build"].width == 0.0

    # A count nobody measured has no ratio at all, which is `bar`'s rule and not a second one.
    unmeasured, _ = board_regions.loop(readings("no-phase-v1.json"))
    assert all(phase.share is None for phase in unmeasured)


def test_a_units_section_carrying_no_phase_says_so_rather_than_showing_noughts() -> None:
    """The producer's state at the time this was written, and a nought would have lied."""
    phases, note = board_regions.loop(readings("no-phase-v1.json"))
    assert [phase.count for phase in phases] == [None] * len(board_regions.PHASES)
    assert not any(phase.here for phase in phases)
    assert f"phase {board_wall.ABSENT_TEXT}" in note
    assert "12 units" in note, "the note does not say how many units it looked at"
    assert f"lanes {board_wall.ABSENT_TEXT}" in note


def test_a_phase_the_harness_does_not_declare_is_appended_rather_than_dropped() -> None:
    """The schema leaves `phase` an open string because a foreign harness names its own."""
    foreign = [{"id": "x-1", "phase": "triage"}, {"id": "x-2", "phase": "build"}]
    phases, _ = board_regions.loop(_reads("wall-v1.json", units=foreign))
    assert [phase.name for phase in phases][-1] == "triage"
    assert next(phase.count for phase in phases if phase.name == "build") == 1


def test_the_running_row_draws_one_card_per_lane_and_names_what_it_dropped() -> None:
    """One card per lane, no reserved frames, and the cap still holds above six.

    The reserved slot is the thing being removed: it kept the row's shape at one lane and at
    six by announcing nothing in the other five, which on this repository's own wall is 40% of
    the screen. The cap survives because six live lanes still have to fit the row.
    """
    cards, dropped, note = board_regions.flight(_reads("wall-v1.json"))
    assert [card.title for card in cards] == [
        "basicly-rbnz49",
        "basicly-f3tked",
        "basicly-7bur",
        "basicly-4t9z",
    ], "the row drew a slot the producer gave it no lane for"
    assert not dropped and not note

    lanes = [{"id": f"lane-{index}", "phase": "build"} for index in range(9)]
    cards, dropped, _ = board_regions.flight(_reads("wall-v1.json", lanes=lanes))
    assert len(cards) == board_regions.FLIGHT_SLOTS
    assert dropped == f"+{9 - board_regions.FLIGHT_SLOTS} more lanes"


def test_no_lane_dispatched_draws_no_card_and_says_which_of_its_two_silences_it_is() -> None:
    """Three ways to have nothing running, and only one of them is a producer that measured.

    The note is what the collapsed row prints in place of the cards, so it has to carry the
    difference: a producer that omitted `lanes` said nothing about what is running, while one
    that emitted an empty list said nothing is.
    """
    reads = _reads("wall-v1.json")
    for empty, expected in (
        (_reads("wall-v1.json", lanes=[]), "no lane is dispatched"),
        (_absent("lanes", reads), board_wall.ABSENT_TEXT),
    ):
        cards, dropped, note = board_regions.flight(empty)
        assert cards == (), "a collapsed row still reserved a card"
        assert not dropped
        assert note == expected


def test_a_lane_card_draws_a_context_bar_only_when_both_of_its_terms_are_there() -> None:
    """`context_used` and `context_window` travel together, and one alone draws no bar."""
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"))
    paired = next(card for card in cards if card.title == "basicly-rbnz49")
    lonely = next(card for card in cards if card.title == "basicly-4t9z")
    assert next(cell.bar for cell in paired.cells if cell.label == "context") is not None
    assert next(cell.bar for cell in lonely.cells if cell.label == "context") is None
    assert (
        next(cell.value for cell in lonely.cells if cell.label == "context") == board_wall.UNKNOWN
    )


def test_a_lane_that_is_not_live_is_marked_on_two_channels() -> None:
    """A dashed border and a different glyph, not only a colour."""
    cards, _, _ = board_regions.flight(_reads("wall-v1.json"))
    live = next(card for card in cards if card.title == "basicly-rbnz49").state
    last_known = next(card for card in cards if card.title == "basicly-4t9z").state
    assert (live.glyph, live.border_style) != (last_known.glyph, last_known.border_style)


def test_the_ready_set_is_ranked_with_priority_and_id_and_title() -> None:
    """Ranked rather than merely listed: an unordered ready set is a list nobody can act on."""
    listing = board_regions.next_up(_reads("wall-v1.json"))
    assert listing.state.key == board_wall.RENDERABLE
    # A slot is a line, and a group heading is a line, so the two share the one budget.
    assert len(listing.rows) + len(listing.groups) == board_regions.READY_SLOTS
    assert [row.priority for row in listing.rows] == sorted(row.priority for row in listing.rows)
    assert all(row.ident and row.title for row in listing.rows)
    assert listing.more == "+6 more ready"
    assert not listing.note


def test_the_reclaimed_width_leaves_the_top_ready_titles_untruncated() -> None:
    """The criterion, and the reason the list has two shapes rather than one.

    The narrow column beside the lanes truncates at 62 characters because that is what fits
    there; the reclaimed width does not, and the assertion is the *pair* - a single bound wide
    enough for the reclaimed row would run off the end of the column. The fixture's own longest
    ready title is the control: without one over 62 characters neither half discriminates.
    """
    long = "the board goes backwards the moment a supervisor starts and in flight has no producer"
    row = {"ready": True, "priority": "P0", "title": long}
    units = [{"id": f"u-{n:02d}", **row} for n in range(20)]
    assert len(long) > board_wall.TITLE_MAX, "the probe cannot see a truncation"

    narrow = board_regions.next_up(_reads("wall-v1.json", units=units))
    assert all(row.title.endswith("\N{HORIZONTAL ELLIPSIS}") for row in narrow.rows)

    wide = board_regions.next_up(_reads("wall-v1.json", units=units), wide=True)
    assert [row.title for row in wide.rows[:3]] == [long] * 3
    assert len(wide.rows) > len(narrow.rows), "the reclaimed height drew no more rows"
    assert len(wide.rows) + len(wide.groups) == board_regions.READY_SLOTS_WIDE


def test_the_ready_region_tells_three_absences_apart_and_none_of_them_is_a_zero() -> None:
    """The middle case is the one a count would have reported as "0 ready"."""
    absent = board_regions.next_up(_absent("units", _reads("wall-v1.json")))
    assert absent.note == board_wall.ABSENT_TEXT
    assert not absent.rows

    unflagged = board_regions.next_up(_reads("wall-v1.json", units=[{"id": "a"}, {"id": "b"}]))
    assert unflagged.note == f"ready {board_wall.ABSENT_TEXT} on any of the 2 units"
    assert unflagged.state.key == board_wall.ABSENT

    none_ready = board_regions.next_up(_reads("wall-v1.json", units=[{"id": "a", "ready": False}]))
    assert none_ready.note == "nothing is ready of the 1 units emitted"
    assert not none_ready.rows


def test_the_status_bar_draws_the_grant_bar_only_against_a_declared_budget() -> None:
    """The catastrophe signal, and it needs both of its numbers like every other bar."""
    cells = board_regions.head(_reads("wall-v1.json"))
    run = next(cell for cell in cells if cell.label == "run")
    assert run.bar is not None and run.bar.over
    assert "basicly-kjc5" in run.value

    no_budget = _reads("wall-v1.json", session={"root": "basicly-kjc5", "spent_tokens": 1})
    run = next(cell for cell in board_regions.head(no_budget) if cell.label == "run")
    assert run.bar is None
    assert run.value == "basicly-kjc5"

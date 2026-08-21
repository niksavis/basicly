"""The footer, and the roster that keeps the layout honest.

Three of these are regressions rather than demonstrations:

* **`dep edges` counted zero over ten real edges.** `graph` is an *object* carrying an
  ``edges`` array, and the first cut read it as a list section - so the wall drew a measured 0
  where the producer had told it ten. The bar rule caught nothing, because a wrong count is
  not a missing term.
* **`health` labelled its rows with the list index.** The agent names the row or the whole
  section is unreadable.
* **The gate strip badged its verdict twice**, once on the title and once on the line below,
  which is the duplication the render this replaces was refused for.
"""

from __future__ import annotations

from basicly import board_footer, board_wall
from tests.test_board_regions import _reads
from tests.test_board_wall import readings


def test_the_dependency_edge_count_is_read_out_of_the_object_the_schema_declares() -> None:
    """The regression: `graph` is an object with an `edges` array, not an array of edges."""
    panel = board_footer.backlog(_reads("wall-v1.json"))
    edges = next(cell for cell in panel.cells if cell.label == "dep edges")
    assert edges.value == "10", "the edge count came back as a zero the producer never gave"


def test_an_absent_graph_says_so_where_a_zero_would_have_read_as_no_edges() -> None:
    """No dependencies, and the producer cannot see dependencies, are different claims."""
    panel = board_footer.backlog(readings("no-phase-v1.json"))
    edges = next(cell for cell in panel.cells if cell.label == "dep edges")
    assert edges.value == board_wall.ABSENT_TEXT
    assert edges.state is not None and edges.state.key == board_wall.ABSENT


def test_the_closed_bar_needs_both_of_its_numbers() -> None:
    """The raw count and no bar once the denominator goes, asserted on the panel."""
    panel = board_footer.backlog(_reads("wall-v1.json"))
    closed = next(cell for cell in panel.cells if cell.label == "closed")
    assert closed.value == "770"
    assert closed.bar is not None and closed.bar.label == "76%"

    held = dict(_reads("wall-v1.json")["backlog"].fields)
    held.pop("total")
    cells = board_footer.backlog(_reads("wall-v1.json", backlog=held)).cells
    closed = next(cell for cell in cells if cell.label == "closed")
    assert closed.value == "770"
    assert closed.bar is None
    assert next(cell.value for cell in cells if cell.label == "total") == board_wall.UNKNOWN


def test_the_priority_histogram_is_sorted_and_each_bar_is_a_share_of_the_counted_set() -> None:
    """Unsorted was one of the six named defects: P3 above P0 is not a histogram."""
    cells = board_footer.priorities(_reads("wall-v1.json"))
    assert [cell.label for cell in cells] == ["P0", "P1", "P2", "P3"]
    assert [cell.value for cell in cells] == ["6", "121", "94", "21"]
    assert all(cell.bar is not None for cell in cells)
    widths = [cell.bar.width for cell in cells if cell.bar]
    assert widths[1] > widths[2] > widths[3] > widths[0]


def test_a_backlog_with_no_priority_map_draws_no_histogram_rather_than_an_empty_one() -> None:
    """An empty axis claims a measurement of nothing; drawing nothing claims nothing."""
    assert board_footer.priorities(_reads("wall-v1.json", backlog={"total": 3})) == ()


def test_the_gate_strip_carries_one_verdict_badge_and_a_state_per_check() -> None:
    """One badge, on the title. The three check states must each be distinguishable."""
    gates, spend, health = board_footer.strips(_reads("wall-v1.json"))
    assert gates.state.key == board_wall.FAIL, "a failing pass flag did not reach the title"
    mode = next(cell for cell in gates.cells if cell.label == "mode")
    assert mode.state is None, "the verdict is badged twice, once on the title and once below"
    keyed = {cell.label: cell.state.key for cell in gates.cells if cell.state}
    assert keyed["pytest"] == board_wall.FAIL
    assert keyed["ruff"] == board_wall.RENDERABLE
    assert keyed["docs-claims"] == board_wall.ABSENT, "a check that did not run reads as passing"
    assert spend.cells[0].value == "machine-local", "scope is drawn verbatim"
    assert health.cells[0].label == "claude", "the agent names the row, not the list index"


def test_a_strip_the_producer_did_not_emit_reads_absent_rather_than_showing_an_empty_row() -> None:
    """Three strips, three absences, and each says which producer did not emit it."""
    gates, spend, health = board_footer.strips(readings("no-phase-v1.json"))
    assert gates.cells, "the fixture does emit gates"
    assert (spend.note, health.note) == (board_wall.ABSENT_TEXT, board_wall.ABSENT_TEXT)
    assert not spend.cells and not health.cells


def test_the_event_ticker_reads_newest_first_and_reports_what_it_did_not_draw() -> None:
    """The dropped count comes back beside the lines, because the row height is fixed."""
    lines, dropped = board_footer.events(_reads("wall-v1.json"))
    assert len(lines) == board_footer.EVENT_LINES
    assert "pytest failed" in lines[0], "the ticker is not newest first"
    assert dropped == "+2 more events"


def test_the_roster_covers_every_section_the_verdict_named_and_the_key_spells_absence() -> None:
    """The accounting that lets a region read several sections or none without losing one."""
    reads = readings("no-phase-v1.json")
    roster = board_footer.inventory(reads)
    assert [cell.label for cell in roster] == list(reads)
    assert len(roster) == 12, "a section the schema declares is missing from the roster"
    drawn = [cell.label for cell in roster if cell.state and cell.state.key == board_wall.ABSENT]
    assert drawn == ["session", "lanes", "asks", "spend", "health", "graph"]
    assert [cell.value for cell in board_footer.legend()][-1] == board_wall.ABSENT_TEXT

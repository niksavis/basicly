"""The footer, and the roster that keeps the layout honest.

Three of these are regressions rather than demonstrations:

* **`dep edges` counted zero over ten real edges.** `graph` is an *object* carrying an
  ``edges`` array, and the first cut read it as a list section - so the wall drew a measured 0
  where the producer had told it ten. The bar rule caught nothing, because a wrong count is
  not a missing term.
* **`health` labelled its rows with the list index.** The agent names the row or the whole
  section is unreadable.
* **The gate strip drew 36 named checks.** Two of them wrapped and were painted over the two
  below; before that, four more pushed `health` off the footer entirely. Both were the same
  fault - a debugging view forced onto a wall - and the token below is the fix that deletes
  the class rather than sizing the grid again. What is asserted is therefore the *reduction*:
  one word when everything passes, and only the exceptions named when it does not.
"""

from __future__ import annotations

from basicly import board_footer, board_wall, config
from tests.test_board_regions import _reads
from tests.test_board_wall import REPO_ROOT, document, readings


def test_the_dependency_edge_count_is_read_out_of_the_object_the_schema_declares() -> None:
    """The regression: `graph` is an object with an `edges` array, not an array of edges."""
    cells = board_footer.backlog(_reads("wall-v1.json"))
    edges = next(cell for cell in cells if cell.label == "dep edges")
    assert edges.value == "10", "the edge count came back as a zero the producer never gave"


def test_an_absent_graph_says_so_where_a_zero_would_have_read_as_no_edges() -> None:
    """No dependencies, and the producer cannot see dependencies, are different claims."""
    cells = board_footer.backlog(readings("no-phase-v1.json"))
    edges = next(cell for cell in cells if cell.label == "dep edges")
    assert edges.value == board_footer._NOT_IN_SNAPSHOT
    assert "producer" not in edges.value and "emitted" not in edges.value
    assert edges.state is not None and edges.state.key == board_wall.ABSENT


def test_the_closed_bar_needs_both_of_its_numbers() -> None:
    """The raw count and no bar once the denominator goes, asserted on the panel."""
    cells = board_footer.backlog(_reads("wall-v1.json"))
    closed = next(cell for cell in cells if cell.label == "closed")
    assert closed.value == "770"
    assert closed.bar is not None and closed.bar.label == "76%"

    held = dict(_reads("wall-v1.json")["backlog"].fields)
    held.pop("total")
    cells = board_footer.backlog(_reads("wall-v1.json", backlog=held))
    closed = next(cell for cell in cells if cell.label == "closed")
    assert closed.value == "770"
    assert closed.bar is None
    assert next(cell.value for cell in cells if cell.label == "total") == board_wall.UNKNOWN


def test_the_priority_histogram_is_sorted_and_each_bar_is_a_share_of_the_counted_set() -> None:
    """Unsorted was one of the six named defects: P3 above P0 is not a histogram."""
    cells, dropped = board_footer.priorities(_reads("wall-v1.json"))
    assert [cell.label for cell in cells] == ["P0", "P1", "P2", "P3"]
    assert [cell.value for cell in cells] == ["6", "121", "94", "21"]
    assert not dropped, "four labels under an eight-slot histogram dropped nothing"
    assert all(cell.bar is not None for cell in cells)
    widths = [cell.bar.width for cell in cells if cell.bar]
    assert widths[1] > widths[2] > widths[3] > widths[0]


def test_a_backlog_with_no_priority_map_draws_no_histogram_rather_than_an_empty_one() -> None:
    """An empty axis claims a measurement of nothing; drawing nothing claims nothing."""
    assert board_footer.priorities(_reads("wall-v1.json", backlog={"total": 3})) == ((), "")


def test_a_failing_gate_set_names_the_failures_and_only_the_failures() -> None:
    """The token, and the whole point of it: one word green, the exceptions spelled.

    Asserted as an exclusion as well as an inclusion, because the defect being deleted is the
    36 names - a token that named the passes too would satisfy an assertion about `pytest`.
    """
    cell, caption = board_footer.gates(_reads("wall-v1.json"))
    assert cell.value == "1 FAILING: pytest"
    assert cell.state is not None and cell.state.key == board_wall.FAIL
    assert "ruff" not in cell.value, "a passing check named itself on a wall"
    assert caption == "mode full \N{MIDDLE DOT} recorded 2026-08-21T16:42:12Z"


def test_a_gate_set_that_wholly_passes_costs_one_word() -> None:
    """The principle, at the one place it reclaims 15% of the screen."""
    passing = {"passed": True, "checks": [{"name": "ruff", "status": "pass"}]}
    cell, _ = board_footer.gates(_reads("wall-v1.json", gates=passing))
    assert cell.value == "GREEN"
    assert cell.state is not None and cell.state.key == board_wall.RENDERABLE


def test_a_check_that_did_not_run_is_its_own_exception_and_names_itself() -> None:
    """Not-run is not passing, and a green token over an unrun suite is the fail-open read."""
    checks = [{"name": "ruff", "status": "pass"}, {"name": "pytest", "status": "not_run"}]
    cell, _ = board_footer.gates(_reads("wall-v1.json", gates={"passed": True, "checks": checks}))
    assert cell.value == "1 NOT RUN: pytest"
    assert cell.state is not None and cell.state.key == board_wall.ABSENT


def test_a_gate_set_whose_failures_outrun_the_token_says_how_many_it_did_not_name() -> None:
    """The one arrangement that would put every name back on the wall: everything failed."""
    checks = [{"name": f"check-{n}", "status": "fail"} for n in range(9)]
    cell, _ = board_footer.gates(_reads("wall-v1.json", gates={"passed": False, "checks": checks}))
    assert cell.value.startswith("9 FAILING: check-0")
    assert cell.value.endswith("+5 more checks")
    assert "check-4" not in cell.value


def test_the_spend_figures_each_carry_the_unit_they_are_denominated_in() -> None:
    """Four bare numbers in a row is a quantity nobody can name; scope stays verbatim."""
    value = board_footer.spend(_reads("wall-v1.json")).value
    assert value.startswith("machine-local"), "scope is drawn verbatim and first"
    assert "1,254.26 usd" in value, "a currency figure was drawn as a bare float"
    assert "48.1M in" in value


def test_the_agent_health_row_is_named_by_its_agent_and_not_by_its_index() -> None:
    """The named defect: a row labelled with the list position is unreadable."""
    cells, dropped = board_footer.health(_reads("wall-v1.json"))
    assert cells[0].label == "claude"
    assert not dropped


def test_a_section_the_producer_did_not_emit_reads_absent_rather_than_an_empty_row() -> None:
    """Three readings, three absences, and each says so in a reader's own vocabulary."""
    reads = readings("no-phase-v1.json")
    absent = board_footer._NOT_IN_SNAPSHOT
    assert board_footer.gates(reads)[0].value != absent, "the fixture emits gates"
    assert board_footer.spend(reads).value == absent
    assert board_footer.health(reads)[0][0].value == absent
    for word in ("producer", "emitted", "section", "withheld"):
        assert word not in absent, f"{word!r} is engine vocabulary a dashboard reader lacks"


def test_the_event_ticker_reads_newest_first_and_reports_what_it_did_not_draw() -> None:
    """The dropped count comes back beside the lines, because the row height is fixed."""
    lines, dropped = board_footer.events(_reads("wall-v1.json"))
    assert len(lines) == board_footer.EVENT_LINES
    assert "pytest failed" in lines[0], "the ticker is not newest first"
    assert dropped == "+4 more events"


def test_the_gate_token_does_not_grow_with_the_check_count_at_all() -> None:
    """The fix, asserted as the thing the sized grid could never give: independence.

    Forty checks against thirteen. The old strip's answer was a reserved 6x6 grid and a
    `+4 more checks` marker; this one draws the same number of characters either way, which is
    why no check name can be painted over another one again. The tree's own longest name is the
    control - a token that still named a passing check would grow with it.
    """
    few, _ = board_footer.gates(_reads("wall-v1.json"))
    many, _ = board_footer.gates(readings("dense-v1.json"))
    assert len(document("dense-v1.json")["gates"]["checks"]) == 40, "the probe is blunt"
    assert few.value == many.value == "1 FAILING: pytest"
    longest = max((check.name for check in config.load_verify_config(REPO_ROOT).checks), key=len)
    assert longest not in many.value, f"the tree's longest name {longest!r} reached the wall"


def test_the_health_row_caps_its_agents_and_names_the_ones_it_did_not_draw() -> None:
    """One cell per agent over an open-ended list is the gate grid's defect in another key."""
    cells, dropped = board_footer.health(readings("dense-v1.json"))
    assert len(cells) == board_footer.HEALTH_SLOTS
    assert dropped == "+2 more agents"


def test_the_throughput_figure_counts_records_closed_on_the_documents_own_day() -> None:
    """Distinct records, dated against the document rather than the reader.

    Four discriminators in one probe, each of which a naive count gets wrong: a status event
    from yesterday, a status event that is not a close, a record closed twice, and an event of
    another kind entirely.
    """
    rows = [
        {"at": "2026-08-21T09:00:00Z", "issue": "a", "kind": "status", "text": "closed"},
        {"at": "2026-08-21T11:00:00Z", "issue": "a", "kind": "status", "text": "closed"},
        {"at": "2026-08-21T12:00:00Z", "issue": "b", "kind": "status", "text": "closed"},
        {"at": "2026-08-20T12:00:00Z", "issue": "c", "kind": "status", "text": "closed"},
        {"at": "2026-08-21T13:00:00Z", "issue": "d", "kind": "status", "text": "in_progress"},
        {"at": "2026-08-21T14:00:00Z", "issue": "e", "kind": "merge", "text": "closed"},
    ]
    cell = board_footer.throughput(_reads("wall-v1.json", events=rows), "2026-08-21")
    assert cell.value == "2"


def test_a_producer_that_records_no_status_event_reports_no_throughput_rather_than_a_zero() -> None:
    """The criterion's other half: absent, never nought. `wall-v1.json` records none."""
    absent = board_footer.throughput(_reads("wall-v1.json"), "2026-08-21")
    assert absent.value == board_wall.UNKNOWN
    assert absent.state is not None and absent.state.key == board_wall.ABSENT
    # The positive control: the same reading with one status row in it does produce a figure.
    rows = [{"at": "2026-08-21T09:00:00Z", "issue": "a", "kind": "status", "text": "opened"}]
    measured = board_footer.throughput(_reads("wall-v1.json", events=rows), "2026-08-21")
    assert measured.value == "0", "a producer that measured nothing closed did measure"


def test_the_priority_histogram_caps_a_vocabulary_the_schema_declines_to_close() -> None:
    """`by_priority` is keyed by the producer's own labels, so ten of them is a legal document."""
    cells, dropped = board_footer.priorities(readings("dense-v1.json"))
    assert len(cells) == board_footer.PRIORITY_SLOTS
    assert dropped == "+2 more priorities"
    assert [cell.label for cell in cells[-1:]] == ["P7"], "the histogram is no longer sorted"


def test_the_roster_covers_every_section_the_verdict_named_and_the_key_spells_absence() -> None:
    """The accounting that lets a region read several sections or none without losing one.

    It now names only what did NOT draw. Naming all twelve spent a standing row saying twelve
    things are normal, and a mark almost always present carries nothing; the audit purpose is
    unchanged, because a section the schema declares and the producer omits is still named
    here and cannot be dropped by a change of layout. The legend went with the glyphs it
    existed to explain - each cell now carries its own word.
    """
    reads = readings("no-phase-v1.json")
    roster = board_footer.inventory(reads)
    absent = ["session", "lanes", "asks", "spend", "health", "graph"]
    assert [cell.label for cell in roster] == absent
    assert all(cell.value for cell in roster), "a named section carries no word for why"
    assert board_footer._NOT_IN_SNAPSHOT in [cell.value for cell in roster]
    # The regression: the row used to spell the bare state key, "withheld", for a withheld
    # section, and board_wall.ABSENT_TEXT - "not emitted by this producer" - for an absent one.
    # Neither word is in a reader's vocabulary, and the bare key threw away the schema's own
    # violation reason a withheld section carries.
    for cell in roster:
        for word in ("producer", "emitted", "section", "withheld"):
            assert word not in cell.value, f"{cell.label} spells engine vocabulary {word!r}"
    # The control: a section that drew is deliberately not named, which is the whole change.
    assert "backlog" not in [cell.label for cell in roster]
    assert not hasattr(board_footer, "legend")


def test_a_withheld_section_spells_its_own_violation_rather_than_the_bare_state_key() -> None:
    """`inventory` used to draw the literal word "withheld", dropping the schema's own reason.

    `broken-section-v1.json`'s `units` section is withheld over one title that broke the
    two-hundred-character bound; that reason is what a reader without the schema can act on,
    and it is what the bare state key threw away.
    """
    reads = readings("broken-section-v1.json")
    units = next(cell for cell in board_footer.inventory(reads) if cell.label == "units")
    assert units.value == reads["units"].note
    assert "too long" in units.value, "the schema's own violation reason did not survive"
    assert units.value != "withheld"

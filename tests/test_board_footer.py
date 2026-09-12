from __future__ import annotations

from basicly import board_footer, board_wall, config
from tests.test_board_regions import _reads
from tests.test_board_wall import REPO_ROOT, STAMPED, document, readings


def _age(name: str = "wall-v1.json") -> board_wall.Age:

    return board_wall.age(document(name), STAMPED)


def test_the_closed_bar_needs_both_of_its_numbers() -> None:
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
    cells, dropped = board_footer.priorities(_reads("wall-v1.json"))
    assert [cell.label for cell in cells] == ["P0", "P1", "P2", "P3"]
    assert [cell.value for cell in cells] == ["6", "121", "94", "21"]
    assert not dropped, "four labels under an eight-slot histogram dropped nothing"
    assert all(cell.bar is not None for cell in cells)
    widths = [cell.bar.width for cell in cells if cell.bar]
    assert widths[1] > widths[2] > widths[3] > widths[0]


def test_a_backlog_with_no_priority_map_draws_no_histogram_rather_than_an_empty_one() -> None:
    assert board_footer.priorities(_reads("wall-v1.json", backlog={"total": 3})) == ((), "")


def test_a_failing_gate_set_names_the_failures_and_only_the_failures() -> None:

    cell, caption = board_footer.gates(_reads("wall-v1.json"), _age())
    assert cell.value == "1 FAILING: pytest"
    assert cell.state is not None and cell.state.key == board_wall.FAIL
    assert "ruff" not in cell.value, "a passing check named itself on a wall"
    assert caption == (
        "mode full \N{MIDDLE DOT} recorded 2026-08-21T16:42:12Z"
        " \N{MIDDLE DOT} taken 40s before this snapshot"
    )


def test_a_gate_set_that_wholly_passes_costs_one_word() -> None:
    passing = {"passed": True, "checks": [{"name": "ruff", "status": "pass"}]}
    cell, _ = board_footer.gates(_reads("wall-v1.json", gates=passing), _age())
    assert cell.value == "GREEN"
    assert cell.state is not None and cell.state.key == board_wall.RENDERABLE


def test_a_check_that_did_not_run_is_its_own_exception_and_names_itself() -> None:
    checks = [{"name": "ruff", "status": "pass"}, {"name": "pytest", "status": "not_run"}]
    cell, _ = board_footer.gates(
        _reads("wall-v1.json", gates={"passed": True, "checks": checks}), _age()
    )
    assert cell.value == "1 NOT RUN: pytest"
    assert cell.state is not None and cell.state.key == board_wall.ABSENT


def test_a_gate_set_whose_failures_outrun_the_token_says_how_many_it_did_not_name() -> None:
    checks = [{"name": f"check-{n}", "status": "fail"} for n in range(9)]
    cell, _ = board_footer.gates(
        _reads("wall-v1.json", gates={"passed": False, "checks": checks}), _age()
    )
    assert cell.value.startswith("9 FAILING: check-0")
    assert cell.value.endswith("+5 more checks")
    assert "check-4" not in cell.value


def test_the_spend_figures_each_carry_the_unit_they_are_denominated_in() -> None:

    value = board_footer.spend(_reads("wall-v1.json")).value
    assert value.startswith("machine-local"), "scope is drawn verbatim and first"
    assert "1,254.26 usd lifetime" in value, "a currency figure was drawn as a bare float"
    assert " in" not in value, "a lifetime token total is back, and it reads as fresh input"


def test_the_agent_health_row_is_named_by_its_agent_and_not_by_its_index() -> None:
    cells, dropped = board_footer.health(_reads("wall-v1.json"))
    assert cells[0].label == "claude"
    assert not dropped


def test_a_section_the_producer_did_not_emit_reads_absent_rather_than_an_empty_row() -> None:
    reads = readings("no-phase-v1.json")
    absent = board_footer._NOT_IN_SNAPSHOT
    assert board_footer.gates(reads, _age())[0].value != absent, "the fixture emits gates"
    assert board_footer.spend(reads).value == absent
    assert board_footer.health(reads)[0][0].value == absent
    for word in ("producer", "emitted", "section", "withheld"):
        assert word not in absent, f"{word!r} is engine vocabulary a dashboard reader lacks"


def test_the_event_ticker_reads_newest_first_and_reports_what_it_did_not_draw() -> None:
    lines, dropped = board_footer.events(_reads("wall-v1.json"))
    assert len(lines) == board_footer.EVENT_LINES
    assert "pytest failed" in lines[0].text, "the ticker is not newest first"
    assert dropped == "+4 more events"
    assert lines[0].ident.startswith("basicly-"), "the ticker names no record"
    assert lines[0].ident not in lines[0].text, "the id is drawn twice"


def test_the_gate_token_does_not_grow_with_the_check_count_at_all() -> None:

    few, _ = board_footer.gates(_reads("wall-v1.json"), _age())
    many, _ = board_footer.gates(readings("dense-v1.json"), _age("dense-v1.json"))
    assert len(document("dense-v1.json")["gates"]["checks"]) == 40, "the probe is blunt"
    assert few.value == many.value == "1 FAILING: pytest"
    longest = max((check.name for check in config.load_verify_config(REPO_ROOT).checks), key=len)
    assert longest not in many.value, f"the tree's longest name {longest!r} reached the wall"


def test_the_health_row_caps_its_agents_and_names_the_ones_it_did_not_draw() -> None:
    cells, dropped = board_footer.health(readings("dense-v1.json"))
    assert len(cells) == board_footer.HEALTH_SLOTS
    assert dropped == "+2 more agents"


def test_the_throughput_figure_counts_records_closed_on_the_documents_own_day() -> None:

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
    absent = board_footer.throughput(_reads("wall-v1.json"), "2026-08-21")
    assert absent.value == board_wall.UNKNOWN
    assert absent.state is not None and absent.state.key == board_wall.ABSENT
    rows = [{"at": "2026-08-21T09:00:00Z", "issue": "a", "kind": "status", "text": "opened"}]
    measured = board_footer.throughput(_reads("wall-v1.json", events=rows), "2026-08-21")
    assert measured.value == "0", "a producer that measured nothing closed did measure"


def _counted(held: object) -> dict[str, board_wall.Reading]:
    return _reads(
        "wall-v1.json",
        backlog={**_reads("wall-v1.json")["backlog"].fields, board_footer.COUNTED_KEY: held},
    )


def test_the_count_the_producer_folded_is_read_before_the_events_tail() -> None:

    rows = [{"at": "2026-08-21T09:00:00Z", "issue": "a", "kind": "status", "text": "closed"}]
    reads = _counted(20)
    reads["events"] = _reads("wall-v1.json", events=rows)["events"]

    assert board_footer.throughput(reads, "2026-08-21").value == "20"


def test_a_counted_day_with_no_close_draws_a_measured_zero() -> None:
    measured = board_footer.throughput(_counted(0), "2026-08-21")
    assert measured.value == "0"
    assert measured.state is not None and measured.state.key == board_wall.RENDERABLE

    unmeasured = board_footer.throughput(_reads("wall-v1.json"), "2026-08-21")
    assert unmeasured.value == board_wall.UNKNOWN


def test_a_count_that_is_not_a_whole_number_falls_through_to_the_tail() -> None:
    for held in (True, "20", 20.5, None):
        assert board_footer.throughput(_counted(held), "2026-08-21").value == board_wall.UNKNOWN


def test_the_priority_histogram_caps_a_vocabulary_the_schema_declines_to_close() -> None:
    cells, dropped = board_footer.priorities(readings("dense-v1.json"))
    assert len(cells) == board_footer.PRIORITY_SLOTS
    assert dropped == "+2 more priorities"
    assert [cell.label for cell in cells[-1:]] == ["P7"], "the histogram is no longer sorted"


def test_the_roster_covers_every_section_the_verdict_named_and_the_key_spells_absence() -> None:

    reads = readings("no-phase-v1.json")
    roster = board_footer.inventory(reads)
    absent = ["session", "lanes", "asks", "spend", "health", "detail", "graph"]
    assert [cell.label for cell in roster] == absent
    assert all(cell.value for cell in roster), "a named section carries no word for why"
    assert board_footer._NOT_IN_SNAPSHOT in [cell.value for cell in roster]
    for cell in roster:
        for word in ("producer", "emitted", "section", "withheld"):
            assert word not in cell.value, f"{cell.label} spells engine vocabulary {word!r}"
    assert "backlog" not in [cell.label for cell in roster]
    assert not hasattr(board_footer, "legend")


def test_a_withheld_section_spells_its_own_violation_rather_than_the_bare_state_key() -> None:

    reads = readings("broken-section-v1.json")
    units = next(cell for cell in board_footer.inventory(reads) if cell.label == "units")
    assert units.value == reads["units"].note
    assert "too long" in units.value, "the schema's own violation reason did not survive"
    assert units.value != "withheld"

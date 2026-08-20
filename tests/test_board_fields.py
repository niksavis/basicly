"""Tests for the board producer's field-selection boundary (basicly-rn0o.2).

Two things are pinned here and both are pinned against evidence outside this file. The
marker roster is bound to `.scripts/check_marker_families.py`, loaded **by file path** the
way `tests/test_check_marker_families.py` loads it, so a thirteenth family fails here as
well as there. The ask pairing is pinned against the frozen corpus under
`tests/fixtures/board/ledger/`, never the live ledger: that log is git-tracked and grew from
980 records to 984 inside two sessions, so an exact count against it is a flaky gate rather
than a regression detector.
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType, SimpleNamespace
from typing import Any

import pytest

from basicly import board_fields, owned_store

REPO_ROOT = Path(__file__).parent.parent
ROSTER_GATE = REPO_ROOT / ".scripts" / "check_marker_families.py"
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger"

# What the frozen corpus holds, and the three numbers are the point. 140 request markers is
# what a tally reports; 1 is what a pairing reports; 203 distinct answered ids is the control
# that fails a parser which silently matched nothing.
# The edges the frozen corpus still asserts: eight written, one of them retracted.
FIXTURE_EDGES = 7

NAIVE_REQUESTS = 140
ANSWERED_IDS = 203
PENDING_ASKS = 1


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone gate script by path, the way `uv run python` does.

    A **path** load and not an import: `.scripts/` is not a package, and its gates import
    into `basicly`, so an import here would invert the gates-to-engine direction. A test may
    reach a gate; the runtime module may not.
    """
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


@pytest.fixture(scope="module")
def fixture_events() -> tuple[Any, list[Any]]:
    """The kit and the frozen corpus's events, read once for every reader that needs both.

    Reached through `owned_store.kit` for the reason :func:`markers` states, and shared so
    the edge assertions below read the log once rather than once each.
    """
    kit = owned_store.kit(REPO_ROOT)
    found, quarantined = kit.events.read_events(FIXTURE_LEDGER)
    assert not quarantined, "the frozen corpus must parse cleanly or it is not a baseline"
    return kit, found


@pytest.fixture(scope="module")
def markers() -> list[board_fields.Marker]:
    """Every marker in the frozen fixture corpus, in ledger read order.

    Reached through `owned_store.kit`, never by loading `events.py` again: that module is
    published under a fixed `sys.modules` name so one process holds one `Event` class, and a
    second load replaces it and breaks
    `test_kit_tracker_provenance.test_the_sibling_event_log_is_loaded_once_under_the_name_it_publishes`.
    """
    found, quarantined = owned_store.kit(REPO_ROOT).events.read_events(FIXTURE_LEDGER)
    assert not quarantined, "the frozen corpus must parse cleanly or it is not a baseline"
    return board_fields.read_markers(found)


def test_the_marker_family_set_equals_the_frozen_roster() -> None:
    """The producer's roster is the gate's roster, or the gate says so."""
    gate = _load(ROSTER_GATE, "check_marker_families")
    frozen = {family.marker for family in gate.FROZEN}
    assert frozen == board_fields.MARKER_FAMILIES


def test_the_roster_carries_the_retired_family_and_branches_on_nothing() -> None:
    """12 frozen: 11 declared plus 1 retired, and the retired one still parses.

    `[harness-overrun]` has no producer left in `src/` and 12 rows still in the log. A
    roster derived from the live constants would drop it and render those rows as nothing,
    which is why the frozen list is the authority and why nothing here reads `retired`.
    """
    gate = _load(ROSTER_GATE, "check_marker_families")
    retired = [family.marker for family in gate.FROZEN if family.retired is not None]
    assert retired == ["[harness-overrun]"]
    assert len(board_fields.MARKER_FAMILIES) == 12
    parsed = board_fields.marker("r-1", "2026-01-01T00:00:00Z", "[harness-overrun] ceiling=200000")
    assert parsed is not None
    assert parsed.family == "[harness-overrun]"
    assert parsed.fields == {"ceiling": "200000"}


def test_every_frozen_family_occurs_in_the_fixture_corpus(
    markers: list[board_fields.Marker],
) -> None:
    """A positive control on the corpus: all 12 families are present to be parsed.

    Without it, a parser that matched only `[harness-wait]` would pass every other
    assertion in this file, because nothing else would have had a row to lose.
    """
    assert {row.family for row in markers} == board_fields.MARKER_FAMILIES


@pytest.mark.parametrize(
    "body",
    [
        "[harness-Wait] id=r-1#wait-x kind=checkpoint requested",
        "[harness-] id=r-1#wait-x",
        "[harness-side] not a family at all",
        "a plain review note with no marker on it",
        "",
        "   [harness-wait",
    ],
)
def test_a_malformed_marker_is_skipped_rather_than_raised(body: str) -> None:
    """The best-effort contract `policy._parse_wait_event` keeps: None, never an exception."""
    assert board_fields.marker("r-1", "2026-01-01T00:00:00Z", body) is None


def test_a_marker_missing_its_fields_is_still_a_marker() -> None:
    """A family with no header fields parses; it is the *ask* that needs an id and a kind."""
    parsed = board_fields.marker("r-1", "2026-01-01T00:00:00Z", "[harness-wait]")
    assert parsed is not None
    assert parsed.fields == {}
    assert parsed.flags == frozenset()


def test_the_pending_ask_is_a_pairing_and_not_a_tally(
    markers: list[board_fields.Marker],
) -> None:
    """One pending against a naive 140, with the answered control at 203 distinct ids."""
    waits = [row for row in markers if row.family == board_fields.WAIT_FAMILY]
    naive = [
        row for row in waits if board_fields.ANSWERED not in row.flags and row.fields.get("id")
    ]
    answered = {
        row.fields["id"]
        for row in waits
        if "id" in row.fields and board_fields.ANSWERED in row.flags
    }
    assert len(naive) == NAIVE_REQUESTS
    assert len(answered) == ANSWERED_IDS

    pending = board_fields.asks(markers)
    assert len(pending) == PENDING_ASKS
    assert pending[0]["wait_id"] == "fx-root.1#wait-ship"
    assert pending[0]["subject"] == "ship"
    assert pending[0]["issue"] == "fx-root.1"
    assert pending[0]["kind"] == "checkpoint"


def test_an_answer_anywhere_closes_a_wait_whatever_the_comment_order() -> None:
    """Order-independent, `policy._open_wait_stamp`'s rule: comment order is not a clock."""
    answer = "[harness-wait] id=r-1#wait-ship kind=checkpoint answered waited_s=3 by=human"
    request = "[harness-wait] id=r-1#wait-ship kind=checkpoint requested"
    at = "2026-01-01T00:00:00Z"
    for bodies in ((answer, request), (request, answer)):
        rows = [board_fields.marker("r-1", at, body) for body in bodies]
        assert board_fields.asks([row for row in rows if row is not None]) == []


def test_a_wait_with_no_kind_is_skipped_and_still_counts_as_a_request() -> None:
    """Which is why the naive 140 above includes one marker the pairing drops."""
    body = "[harness-wait] id=r-1#wait-x requested"
    row = board_fields.marker("r-1", "2026-01-01T00:00:00Z", body)
    assert row is not None and row.fields == {"id": "r-1#wait-x"}
    assert board_fields.asks([row]) == []


def test_a_wait_whose_stamp_will_not_parse_is_skipped() -> None:
    """A row with no readable request time has no waiting_s, and the schema requires one."""
    row = board_fields.marker("r-1", "not a timestamp", "[harness-wait] id=r-1#w kind=decision")
    assert row is not None
    assert board_fields.asks([row]) == []


def test_a_naive_ledger_stamp_is_read_as_utc() -> None:
    """Guessing the local zone would turn a missing suffix into an hours-wrong interval."""
    assert board_fields.instant("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert board_fields.instant("2026-01-01T00:00:00+02:00") == datetime(
        2026, 1, 1, tzinfo=UTC
    ) - timedelta(hours=2)
    assert board_fields.instant("whenever") is None


def test_a_value_is_redacted_before_it_is_bounded() -> None:
    """Both, in that order: a truncated path would still publish the username at its head."""
    leaked = board_fields.text("built at /home/someone/checkout/src", board_fields.TEXT_MAX)
    assert "/home/someone" not in leaked
    assert board_fields.text("x" * 500, board_fields.KIND_MAX) == "x" * board_fields.KIND_MAX


def test_the_event_strip_carries_declared_fields_and_never_a_body(
    markers: list[board_fields.Marker],
) -> None:
    """A row is its family and its `key=value` fields - the 132.5x rule, at one row."""
    rows = board_fields.events(markers, 3)
    assert len(rows) == 3
    assert rows[-1] == {
        "at": "2026-01-01T00:06:14Z",
        "issue": "fx-root.4",
        "kind": "harness-sizing",
        "text": "scope_tokens=1200",
    }


def test_a_supplied_lane_row_carries_the_callers_phase_and_never_a_derived_one() -> None:
    """basicly-06pvsc: `phase` reaches the row from the caller, unchanged and unaudited.

    `verify` is the discriminating value. It is the rung `loop_state.derive_phase` reaches
    only through `policy.GateStatus`, which reads the required-gate set this producer does
    not open - so a reducer that had guessed from ledger evidence could not have produced it.
    """
    lane = board_fields.LaneFacts(
        id="fx-root.1",
        phase="verify",
        status="in_progress",
        agent="claude",
        live=True,
        started_at="2026-01-01T00:00:04Z",
        tokens=18794333,
        branch="harness/fx-root.1",
    )
    assert board_fields.lanes([lane]) == [
        {
            "id": "fx-root.1",
            "phase": "verify",
            "status": "in_progress",
            "agent": "claude",
            "live": True,
            "started_at": "2026-01-01T00:00:04Z",
            "tokens": 18794333,
            "branch": "harness/fx-root.1",
        }
    ]


def test_a_lane_row_emits_only_what_the_caller_knew() -> None:
    """No zeros and no nulls: an unsupplied lane value is an absent key.

    And a lane missing either value the schema requires is skipped rather than completed,
    because a row invented for a lane whose phase the caller could not read is exactly the
    estimate `LaneFacts` exists to refuse.
    """
    rows = board_fields.lanes([
        board_fields.LaneFacts(id="fx-root.3", phase="build"),
        board_fields.LaneFacts(id="fx-root.4", phase=""),
        board_fields.LaneFacts(id="", phase="ship"),
    ])
    assert rows == [{"id": "fx-root.3", "phase": "build"}]


def test_a_lane_branch_and_an_unparsable_start_are_handled_at_the_producer() -> None:
    """The branch is redacted here, and a stamp that will not parse omits its key.

    The schema names `branch` as the known carrier of a machine username into a published
    document, and the consumer has no redaction pass, so this is the only place it can go.
    """
    rows = board_fields.lanes([
        board_fields.LaneFacts(
            id="fx-root.1", phase="build", branch="/home/someone/wt", started_at="whenever"
        )
    ])
    assert "/home/someone" not in str(rows)
    assert "started_at" not in rows[0]


def test_a_unit_row_carries_the_bounded_title_and_no_other_prose() -> None:
    """basicly-vhixrn: fields, never records - and `title` is the only prose admitted.

    The refusal half is the assertion that matters. A folded record carries its description,
    its acceptance criteria and every comment body; a row shaped like one would put the whole
    log on the wire, which is the 132.5x this module exists for.
    """
    state = SimpleNamespace(
        record="fx-root.1",
        status="in_progress",
        fields={
            "title": "a lane in flight",
            "priority": 1,
            "issue_type": "task",
            "description": "a body no board may carry",
            "acceptance_criteria": "nor these",
        },
    )
    rows = board_fields.units([state])
    assert rows == [
        {
            "id": "fx-root.1",
            "title": "a lane in flight",
            "status": "in_progress",
            "priority": "P1",
            "type": "task",
        }
    ]
    assert "body no board" not in str(rows)
    assert "acceptance_criteria" not in str(rows)


def test_a_unit_row_bounds_a_title_and_omits_what_the_record_lacks() -> None:
    """An over-long title is truncated rather than withholding the whole section.

    A length violation inside an optional section costs that section, so bounding here is
    what keeps a long title from blanking the panel it belongs to.
    """
    long = SimpleNamespace(record="fx-1", status="", fields={"title": "t" * 400, "priority": True})
    rows = board_fields.units([long])
    assert rows[0]["title"] == "t" * board_fields.TEXT_MAX
    assert set(rows[0]) == {"id", "title"}


def test_the_graph_section_is_triples_and_the_edge_kind_passes_through() -> None:
    """The kind is not mapped: the schema leaves it open so a foreign vocabulary survives."""
    section = board_fields.graph([("fx-a", "invented-by-someone-else", "fx-b")])
    assert section == {
        "edges": [{"from": "fx-a", "to": "fx-b", "kind": "invented-by-someone-else"}]
    }


def test_the_edge_reader_agrees_with_the_kits_own_on_the_frozen_corpus(
    fixture_events: tuple[Any, list[Any]],
) -> None:
    """Two producers of one answer, held to one shape.

    `board_fields.edge_triples` reads the edge events directly because
    `views_from_events` folds the log a second time to answer this, and folding once is the
    producer's whole advantage. That makes this parity assertion the thing standing between
    the two readers and a silent dialect drift - the same job `test_tracker_query` does for
    `tracker._edges` against the kit's own `read_record`.
    """
    kit, events = fixture_events
    mine = sorted(board_fields.edge_triples(kit, events))
    views = kit.views_from_events(events)
    theirs = sorted(
        (record, edge.type, edge.target)
        for record, view in views.items()
        for edge in view.dependencies
    )
    assert mine == theirs
    assert len(mine) == FIXTURE_EDGES


def test_a_retracted_edge_is_absent_while_both_of_its_events_remain(
    fixture_events: tuple[Any, list[Any]],
) -> None:
    """The control on the parity test above: the corpus holds an edge and its retraction.

    Without this the two readers could agree by both ignoring retraction, and the assertion
    would pass over the one case it exists for. The positive control is that the *asserted*
    `blocks` edge in the same corpus is present.
    """
    kit, events = fixture_events
    triples = set(board_fields.edge_triples(kit, events))
    kinds = {event.kind for event in events}

    assert kit.events.KIND_EDGE_RETRACTED in kinds
    assert ("fx-root.1", "blocks", "fx-root.5") not in triples
    assert ("fx-root.4", "blocks", "fx-root.3") in triples

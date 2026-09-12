from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from basicly import board_fields, board_sections, owned_store

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger"

NOW = datetime(2026, 1, 2, tzinfo=UTC)

FIXTURE_EDGES = 7

NAIVE_REQUESTS = 140
ANSWERED_IDS = 203
PENDING_ASKS = 1


@pytest.fixture(scope="module")
def fixture_events() -> tuple[Any, list[Any]]:

    kit = owned_store.kit(REPO_ROOT)
    found, quarantined = kit.events.read_events(FIXTURE_LEDGER)
    assert not quarantined, "the frozen corpus must parse cleanly or it is not a baseline"
    return kit, found


@pytest.fixture(scope="module")
def markers() -> list[board_fields.Marker]:

    found, quarantined = owned_store.kit(REPO_ROOT).events.read_events(FIXTURE_LEDGER)
    assert not quarantined, "the frozen corpus must parse cleanly or it is not a baseline"
    return board_fields.read_markers(found)


def test_the_pending_ask_is_a_pairing_and_not_a_tally(
    markers: list[board_fields.Marker],
) -> None:
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

    pending = board_sections.asks(markers, now=NOW)
    assert len(pending) == PENDING_ASKS
    assert pending[0]["wait_id"] == "fx-root.1#wait-ship"
    assert pending[0]["subject"] == "ship"
    assert pending[0]["issue"] == "fx-root.1"
    assert pending[0]["kind"] == "checkpoint"


def test_an_answer_anywhere_closes_a_wait_whatever_the_comment_order() -> None:
    answer = "[harness-wait] id=r-1#wait-ship kind=checkpoint answered waited_s=3 by=human"
    request = "[harness-wait] id=r-1#wait-ship kind=checkpoint requested"
    at = "2026-01-01T00:00:00Z"
    for bodies in ((answer, request), (request, answer)):
        rows = [board_fields.marker("r-1", at, body) for body in bodies]
        assert board_sections.asks([row for row in rows if row is not None], now=NOW) == []


def test_a_wait_with_no_kind_is_skipped_and_still_counts_as_a_request() -> None:
    body = "[harness-wait] id=r-1#wait-x requested"
    row = board_fields.marker("r-1", "2026-01-01T00:00:00Z", body)
    assert row is not None and row.fields == {"id": "r-1#wait-x"}
    assert board_sections.asks([row], now=NOW) == []


def test_a_wait_whose_stamp_will_not_parse_is_skipped() -> None:
    row = board_fields.marker("r-1", "not a timestamp", "[harness-wait] id=r-1#w kind=decision")
    assert row is not None
    assert board_sections.asks([row], now=NOW) == []


def test_a_pending_ask_carries_its_age_and_the_question_only_a_caller_can_supply() -> None:

    row = board_fields.marker(
        "r-1", "2026-01-01T00:00:00Z", "[harness-wait] id=r-1#wait-ship kind=checkpoint"
    )
    assert row is not None

    bare = board_sections.asks([row], now=NOW)
    assert bare[0]["waiting_s"] == 24 * 60 * 60.0
    assert "question" not in bare[0]

    asked = board_sections.asks([row], now=NOW, questions={"r-1#wait-ship": "ship it? " * 200})
    assert asked[0]["question"] == ("ship it? " * 200)[: board_fields.QUESTION_MAX]
    assert board_sections.asks([row], now=NOW, questions={"r-2#wait-ship": "wrong id"}) == bare


def test_a_stamp_ahead_of_now_is_clamped_rather_than_reported_as_negative() -> None:
    row = board_fields.marker(
        "r-1", "2027-01-01T00:00:00Z", "[harness-wait] id=r-1#wait-ship kind=checkpoint"
    )
    assert row is not None
    assert board_sections.asks([row], now=NOW)[0]["waiting_s"] == 0.0


def test_the_repo_section_is_the_name_alone_until_a_caller_runs_git() -> None:
    assert board_sections.repo("basicly", None) == {"name": "basicly"}
    assert board_sections.repo("basicly", board_sections.RepoFacts()) == {"name": "basicly"}
    assert board_sections.repo(
        "basicly", board_sections.RepoFacts(branch="harness/x", head="7c930755", dirty=False)
    ) == {"name": "basicly", "branch": "harness/x", "head": "7c930755", "dirty": False}


def test_the_event_strip_carries_declared_fields_and_never_a_body(
    markers: list[board_fields.Marker],
) -> None:
    rows = board_sections.events(markers, 3)
    assert len(rows) == 3
    assert rows[-1] == {
        "at": "2026-01-01T00:06:14Z",
        "issue": "fx-root.4",
        "kind": "harness-sizing",
        "text": "scope_tokens=1200",
    }


def test_a_supplied_lane_row_carries_the_callers_phase_and_never_a_derived_one() -> None:

    lane = board_sections.LaneFacts(
        id="fx-root.1",
        phase="verify",
        status="in_progress",
        agent="claude",
        live=True,
        started_at="2026-01-01T00:00:04Z",
        tokens=18794333,
        branch="harness/fx-root.1",
    )
    assert board_sections.lanes([lane]) == [
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

    rows = board_sections.lanes([
        board_sections.LaneFacts(id="fx-root.3", phase="build"),
        board_sections.LaneFacts(id="fx-root.4", phase=""),
        board_sections.LaneFacts(id="", phase="ship"),
    ])
    assert rows == [{"id": "fx-root.3", "phase": "build"}]


def test_a_lane_branch_and_an_unparsable_start_are_handled_at_the_producer() -> None:

    rows = board_sections.lanes([
        board_sections.LaneFacts(
            id="fx-root.1", phase="build", branch="/home/someone/wt", started_at="whenever"
        )
    ])
    assert "/home/someone" not in str(rows)
    assert "started_at" not in rows[0]


def test_a_running_lane_row_carries_the_live_figures_and_bounds_the_prose() -> None:

    rows = board_sections.lanes([
        board_sections.LaneFacts(
            id="fx-root.1",
            phase="build",
            live=True,
            model="claude-opus-5",
            note="w" * 500,
            cost_usd=12.5,
            elapsed_s=900.0,
            context_used=180_000,
            context_window=1_000_000,
            rework_attempt=1,
            rework_allowance=2,
        )
    ])
    assert rows[0]["model"] == "claude-opus-5"
    note = rows[0]["note"]
    assert isinstance(note, str)
    assert len(note) <= board_fields.TEXT_MAX
    assert rows[0]["cost_usd"] == 12.5
    assert (rows[0]["elapsed_s"], rows[0]["rework_attempt"]) == (900.0, 1)
    assert (rows[0]["context_used"], rows[0]["context_window"]) == (180_000, 1_000_000)
    assert rows[0]["rework_allowance"] == 2


def test_a_unit_row_carries_the_bounded_title_and_no_other_prose() -> None:

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
    rows = board_sections.units([state])
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

    long = SimpleNamespace(record="fx-1", status="", fields={"title": "t" * 400, "priority": True})
    rows = board_sections.units([long])
    assert rows[0]["title"] == "t" * board_fields.TEXT_MAX
    assert set(rows[0]) == {"id", "title"}


def test_a_unit_row_carries_a_supplied_phase_and_readiness_and_omits_the_rest() -> None:

    states = [
        SimpleNamespace(record="fx-1", status="open", fields={}),
        SimpleNamespace(record="fx-2", status="open", fields={}),
        SimpleNamespace(record="fx-3", status="open", fields={}),
    ]
    rows = board_sections.units(
        states,
        phases={"fx-1": "verify"},
        ready=board_sections.Readiness(ready=frozenset({"fx-1"}), blocked=frozenset({"fx-2"})),
    )
    assert rows == [
        {"id": "fx-1", "status": "open", "phase": "verify", "ready": True},
        {"id": "fx-2", "status": "open", "ready": False},
        {"id": "fx-3", "status": "open"},
    ]


def test_the_graph_section_is_triples_and_the_edge_kind_passes_through() -> None:
    section = board_sections.graph([("fx-a", "invented-by-someone-else", "fx-b")])
    assert section == {
        "edges": [{"from": "fx-a", "to": "fx-b", "kind": "invented-by-someone-else"}]
    }


def test_the_edge_reader_agrees_with_the_kits_own_on_the_frozen_corpus(
    fixture_events: tuple[Any, list[Any]],
) -> None:

    kit, events = fixture_events
    mine = sorted(board_sections.edge_triples(kit, events))
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

    kit, events = fixture_events
    triples = set(board_sections.edge_triples(kit, events))
    kinds = {event.kind for event in events}

    assert kit.events.KIND_EDGE_RETRACTED in kinds
    assert ("fx-root.1", "blocks", "fx-root.5") not in triples
    assert ("fx-root.4", "blocks", "fx-root.3") in triples

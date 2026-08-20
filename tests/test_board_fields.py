"""Tests for the board producer's field-selection boundary (basicly-rn0o.2).

Two things are pinned here and both are pinned against evidence outside this file. The
marker roster is bound to `.scripts/check_marker_families.py`, loaded **by file path** the
way `tests/test_check_marker_families.py` loads it, so a thirteenth family fails here as
well as there. The ask pairing is pinned against the frozen corpus under
`tests/fixtures/board/ledger/`, never the live ledger: that log is git-tracked and grew from
980 records to 984 inside two sessions, so an exact count against it is a flaky gate rather
than a regression detector.

The section reducers moved to `test_board_sections` when `board_fields` split, and the ask
and edge assertions went with them (basicly-y754k2).
"""

from __future__ import annotations

import importlib.util
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from types import ModuleType
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

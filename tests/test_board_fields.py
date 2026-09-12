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

FIXTURE_EDGES = 7

NAIVE_REQUESTS = 140
ANSWERED_IDS = 203
PENDING_ASKS = 1


def _load(path: Path, name: str) -> ModuleType:

    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


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


def test_the_marker_family_set_equals_the_frozen_roster() -> None:
    gate = _load(ROSTER_GATE, "check_marker_families")
    frozen = {family.marker for family in gate.FROZEN}
    assert frozen == board_fields.MARKER_FAMILIES


def test_the_roster_carries_the_retired_family_and_branches_on_nothing() -> None:

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
    assert board_fields.marker("r-1", "2026-01-01T00:00:00Z", body) is None


def test_a_marker_missing_its_fields_is_still_a_marker() -> None:
    parsed = board_fields.marker("r-1", "2026-01-01T00:00:00Z", "[harness-wait]")
    assert parsed is not None
    assert parsed.fields == {}
    assert parsed.flags == frozenset()


def test_a_naive_ledger_stamp_is_read_as_utc() -> None:
    assert board_fields.instant("2026-01-01T00:00:00") == datetime(2026, 1, 1, tzinfo=UTC)
    assert board_fields.instant("2026-01-01T00:00:00+02:00") == datetime(
        2026, 1, 1, tzinfo=UTC
    ) - timedelta(hours=2)
    assert board_fields.instant("whenever") is None


def test_a_value_is_redacted_before_it_is_bounded() -> None:
    leaked = board_fields.text("built at /home/someone/checkout/src", board_fields.TEXT_MAX)
    assert "/home/someone" not in leaked
    assert board_fields.text("x" * 500, board_fields.KIND_MAX) == "x" * board_fields.KIND_MAX

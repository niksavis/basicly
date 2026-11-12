from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.tracker_process_fixture import recorded_review

KIT = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "tracker"


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


commands = _load("basicly_tracker_kit_commands", "commands.py")
holder_writes = commands.holder_writes
events = commands.events

SHAPED = {
    "title": "story",
    "description": "When a child is ready, I want it offered, so I can pick it up.",
    "acceptance_criteria": "- the ready query offers it",
    "requirements": "- standard library only",
}


@pytest.fixture
def story(tmp_path: Path) -> tuple[Path, str]:
    ledger = tmp_path / "ledger"
    record = commands.create_root(ledger, SHAPED, prefix="acme")[0].record
    recorded_review(commands, ledger, record)
    return ledger, record


def _state(ledger: Path, record: str) -> Any:
    return events.fold(events.read_events(ledger)[0]).records[record]


def test_assign_names_the_holder_and_leaves_the_status(story: tuple[Path, str]) -> None:
    ledger, record = story

    holder_writes.assign(ledger, record, "alex")

    state = _state(ledger, record)
    assert state.fields["assignee"] == "alex"
    assert state.status == "open"


def test_claim_names_the_holder_and_starts_the_record(story: tuple[Path, str]) -> None:
    ledger, record = story

    holder_writes.claim(ledger, record, "alex")

    state = _state(ledger, record)
    assert state.fields["assignee"] == "alex"
    assert state.status == "in_progress"


def test_a_claim_without_a_holder_name_is_refused_by_name(story: tuple[Path, str]) -> None:
    ledger, record = story

    with pytest.raises(commands.TrackerCommandError, match="no holder name"):
        holder_writes.claim(ledger, record, "")


def test_only_the_holder_unassigns_unless_take_is_given(story: tuple[Path, str]) -> None:
    ledger, record = story
    holder_writes.claim(ledger, record, "alex")

    with pytest.raises(holder_writes.holders.fields.RefusedFieldError, match="only the holder"):
        holder_writes.unassign(ledger, record, "sam")
    holder_writes.unassign(ledger, record, "sam", take=True)

    assert _state(ledger, record).fields["assignee"] == ""


def test_the_commands_module_still_offers_the_holder_writes(story: tuple[Path, str]) -> None:
    ledger, record = story

    commands.assign(ledger, record, "alex")
    commands.unassign(ledger, record, "alex")

    assert _state(ledger, record).fields["assignee"] == ""

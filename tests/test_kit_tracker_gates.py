"""Tests for the gate kind and the per-issue gate view (basicly-vkh0.26).

Every assertion runs over a real ledger on disk rather than over hand-built events: the
question the bead asks is whether the *store* can answer whether a gate is green, and an
in-memory event list would skip the write, the read and the JSON round trip that a stored
boolean actually crosses.

Two of them reach outside the kit on purpose. `basicly.mirror` is the only writer of this
kind, so the round trip through it is what proves the reader and the writer agree on the
three payload keys; and `differential.gate_verdict` holds the second copy of that
vocabulary, so its answer is compared against this module's on one ledger.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from basicly import mirror, owned_store

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

ISSUE = "basicly-abcd"
OTHER = "basicly-bcde"
ENGINE = "basicly-verify"
FOREIGN = "some-agent"

# 2001-09-09T01:46:40Z, so the recorded time is test data rather than a property of the
# machine that ran it (§9.5: the clock is an injected argument).
CLOCK = 1_000_000_000.0
STAMP = "2001-09-09T01:46:40Z"


def _load(path: Path, name: str) -> ModuleType:
    """Load a standalone kit module by path, the way a consumer without basicly would."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gates = _load(KIT_DIR / "gates.py", "tracker_gates")
# The sibling the module loaded for itself, never a second copy: two loads of `events.py`
# mint two `InvalidEventError` classes and every `except` here stops matching one of them.
events = gates.events


@pytest.fixture(scope="module")
def kit() -> Any:
    """This repo's installed kit, as `basicly.mirror` and `basicly.br` reach it."""
    return owned_store.kit(REPO_ROOT)


def _append(ledger: Path, *drafts: Any) -> list[Any]:
    """Append *drafts* under the fixed clock and return what landed."""
    return events.append(ledger, list(drafts), clock=lambda: CLOCK)


def _read(ledger: Path) -> list[Any]:
    """Every event back off disk, quarantined lines dropped."""
    found, quarantined = events.read_events(ledger)
    assert quarantined == []
    return found


def test_a_gate_result_carries_the_issue_gate_verdict_provider_and_time(tmp_path: Path) -> None:
    """AC1: a gate result appended to the log reads back as a first-class result."""
    ledger = tmp_path / "ledger"
    _append(ledger, gates.gate_draft(ISSUE, "verify", provider=ENGINE, passed=True))

    stored = [event for event in _read(ledger) if gates.is_gate_event(event)]
    assert len(stored) == 1
    result = gates.read_result(stored[0])
    assert (result.record, result.gate, result.provider, result.passed) == (
        ISSUE,
        "verify",
        ENGINE,
        True,
    )
    assert result.ts == STAMP
    assert result.event_id == stored[0].id


@pytest.mark.parametrize(
    ("gate", "provider"),
    [("", ENGINE), ("verify", ""), ("two words", ENGINE), ("verify", "two words")],
)
def test_a_gate_result_missing_a_token_is_refused_on_write(gate: str, provider: str) -> None:
    """A draft is refused before anything is authoritative, not skipped by the fold later."""
    with pytest.raises(gates.InvalidGateError):
        gates.gate_draft(ISSUE, gate, provider=provider, passed=True)


def test_a_verdict_that_is_not_a_boolean_is_refused_on_read(tmp_path: Path) -> None:
    """A truthy string would make ``"false"`` green, so the stored verdict must be a bool.

    Written through `events.append` directly — the draft builder refuses it, and what this
    asserts is that a line some other writer put there is reported rather than believed.
    """
    ledger = tmp_path / "ledger"
    payload = {
        gates.GATE_NAME_KEY: "verify",
        gates.GATE_PROVIDER_KEY: ENGINE,
        gates.GATE_PASSED_KEY: "false",
    }
    _append(ledger, events.Draft(ISSUE, gates.KIND_GATE, payload))

    folded = gates.fold_gates(_read(ledger))
    assert folded.view(ISSUE).results == ()
    assert [found.record for found in folded.malformed] == [ISSUE]
    assert gates.GATE_PASSED_KEY in folded.malformed[0].reason


def test_the_fold_answers_whether_each_required_gate_is_green(tmp_path: Path) -> None:
    """AC2: per issue, per required gate, with a missing gate and a foreign one both red."""
    ledger = tmp_path / "ledger"
    _append(
        ledger,
        gates.gate_draft(ISSUE, "verify", provider=ENGINE, passed=True),
        gates.gate_draft(ISSUE, "rubric", provider=ENGINE, passed=False),
        gates.gate_draft(OTHER, "verify", provider=FOREIGN, passed=True),
    )
    folded = gates.fold_gates(_read(ledger))

    required = ("verify", "rubric", "validate")
    assert folded.view(ISSUE).required_green(required, providers={ENGINE}) == {
        "verify": True,
        "rubric": False,
        "validate": False,
    }
    # The foreign row is kept and simply does not count, so a reader can explain the red.
    other = folded.view(OTHER)
    assert other.required_green(required, providers={ENGINE}) == dict.fromkeys(required, False)
    assert [(row.provider, row.passed) for row in other.results] == [(FOREIGN, True)]
    assert folded.view("basicly-cdef").results == ()


def test_the_latest_result_for_one_gate_and_provider_wins(tmp_path: Path) -> None:
    """A gate that passed and then failed is red, and keeps one row for the pair."""
    ledger = tmp_path / "ledger"
    _append(ledger, gates.gate_draft(ISSUE, "verify", provider=ENGINE, passed=True))
    _append(ledger, gates.gate_draft(ISSUE, "verify", provider=ENGINE, passed=False))

    view = gates.fold_gates(_read(ledger)).view(ISSUE)
    assert len(view.results) == 1
    assert view.green("verify", providers={ENGINE}) is False


def test_an_unknown_gate_kind_is_skipped_for_state_and_still_counted(tmp_path: Path) -> None:
    """AC3: a newer writer's ``gate_waived`` folds no result and is still counted.

    The known kind in the same ledger is the positive control: a reader that dropped every
    gate-family event would satisfy the first half of this on its own.
    """
    ledger = tmp_path / "ledger"
    _append(
        ledger,
        gates.gate_draft(ISSUE, "verify", provider=ENGINE, passed=True),
        events.Draft(ISSUE, "gate_waived", {gates.GATE_NAME_KEY: "rubric"}),
    )
    collected = _read(ledger)

    folded = gates.fold_gates(collected)
    assert folded.unknown_kinds == {"gate_waived": 1}
    assert folded.malformed == []
    assert folded.view(ISSUE).unreadable == ("gate_waived",)
    assert folded.view(ISSUE).green("verify", providers={ENGINE}) is True
    assert folded.view(ISSUE).green("rubric", providers={ENGINE}) is False

    # The event log counts every kind, known or not, so a newer writer's carried totals and
    # an older reader's recomputation agree (`events.accumulate`).
    state = events.fold(collected).records[ISSUE]
    assert state.totals.events == len(collected) == 2


def test_the_mirror_writes_the_kind_this_module_reads(tmp_path: Path, kit: Any) -> None:
    """The dual write is the only writer, so the two ends are held to one vocabulary."""
    assert (gates.KIND_GATE, gates.GATE_NAME_KEY, gates.GATE_PROVIDER_KEY) == (
        kit.KIND_GATE,
        kit.GATE_NAME_KEY,
        kit.GATE_PROVIDER_KEY,
    )
    assert gates.GATE_PASSED_KEY == kit.GATE_PASSED_KEY

    ledger = tmp_path / "ledger"
    argv = ["gate", "report", ISSUE, "--gate", "verify", "--provider", ENGINE, "--status", "pass"]
    _append(ledger, *mirror.drafts(kit, argv, ""))

    assert gates.fold_gates(_read(ledger)).view(ISSUE).green("verify", providers={ENGINE}) is True


def test_the_view_agrees_with_the_differential_verdict(tmp_path: Path, kit: Any) -> None:
    """The second copy of this vocabulary is `differential.gate_verdict`; it must agree.

    Two implementations of one rule is the defect the kit's design names, so the duplication
    is held down by this assertion until basicly-vkh0.27 removes it.
    """
    ledger = tmp_path / "ledger"
    _append(
        ledger,
        gates.gate_draft(ISSUE, "verify", provider=ENGINE, passed=True),
        gates.gate_draft(OTHER, "verify", provider=ENGINE, passed=False),
    )
    collected = _read(ledger)
    vocabulary = kit.Vocabulary()
    views = kit.views_from_events(collected)
    folded = gates.fold_gates(collected)

    for record in (ISSUE, OTHER):
        verdict = kit.gate_verdict(views[record], vocabulary)
        mine = folded.view(record).required_green(
            vocabulary.required_gates, providers=vocabulary.engine_gate_providers
        )
        assert all(mine.values()) is verdict.can_advance

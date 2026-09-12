from __future__ import annotations

import inspect
from typing import TYPE_CHECKING, Any

import pytest

from basicly import decisions, dispatch_brief, loop, policy, runner, tracker, validate_gate
from basicly.config import PolicyConfig, RunnerConfig
from basicly.integrity import VALIDATE_GATE
from basicly.loop_state import NodeState
from basicly.policy import GateStatus
from tests import flipped_tracker

if TYPE_CHECKING:
    from pathlib import Path

CONFIG = PolicyConfig(required_gates=("verify", VALIDATE_GATE), max_rework=2)

RECORD = "unit-1"

OWED = GateStatus(False, ("verify",), (), (VALIDATE_GATE,), (), ())

STATE = NodeState(
    issue_id=RECORD,
    status="in_progress",
    issue_type="task",
    phase="validate",
    worktree=None,
    gates=OWED,
    checkpoints=(),
    rework={},
    has_children=False,
)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(root, RECORD, title="a unit that owes the consumer gate")
    return root


@pytest.fixture(autouse=True)
def _validate_phase(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.setattr(loop.loop_state, "read_node_state", lambda *_a, **_k: STATE)
    monkeypatch.setattr(loop.merge, "commit_tracker_state", lambda *_a, **_k: True)
    monkeypatch.setattr(loop, "record_run", lambda *_a, **_k: None)
    monkeypatch.setattr(loop, "_dispatch_reviews", lambda _ctx: None)
    monkeypatch.setattr(
        loop,
        "load_runner_config",
        lambda *_a: RunnerConfig(specs=runner.BUILTIN_RUNNERS, default="claude"),
    )


def _validator_says(monkeypatch: pytest.MonkeyPatch, reply: str) -> None:
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, stdout=reply
        ),
    )


def gate_events(repo: Path) -> list[Any]:

    kind = tracker.kit(repo).events.KIND_GATE
    return [
        event
        for event in flipped_tracker.ledger_events(repo)
        if event.kind == kind and event.record == RECORD
    ]


def test_a_validator_verdict_records_a_gate_event_on_the_ledger(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _validator_says(
        monkeypatch, "ran `basicly loop status`, it printed the table\nVALIDATION: PASS"
    )
    assert gate_events(repo) == [], "the fixture ledger must start with no verdict"

    result = loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs())

    recorded = gate_events(repo)
    assert len(recorded) == 1
    assert recorded[0].payload["gate"] == VALIDATE_GATE
    assert recorded[0].payload["passed"] is True
    assert result.to_phase == "verify" and not result.blocked


def test_a_reply_with_no_verdict_blocks_on_a_queued_decision(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _validator_says(monkeypatch, "I could not launch the CLI and I am not sure why")

    result = loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs())

    queued = [item for item in decisions.items_by_id(repo, RECORD).values() if item.pending]
    assert len(queued) == 1
    assert queued[0].kind == "validate"
    assert "validator" in queued[0].question
    assert dispatch_brief.VERDICT_PREFIX in queued[0].question
    assert "could not launch the CLI" in queued[0].detail
    assert result.blocked and result.action == "decision" and result.to_phase == "validate"
    assert gate_events(repo) == []


def test_an_undispatched_validator_records_no_gate_event(repo: Path) -> None:

    result = loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs(), repair_dispatch=False)

    assert gate_events(repo) == []
    assert decisions.items_by_id(repo, RECORD) == {}
    assert result.blocked and result.needs_input == "validation"

    validate_gate.record_verdict(repo, RECORD, passed=True)

    assert len(gate_events(repo)) == 1, "the probe must be able to see a gate event"


def test_the_engine_and_not_the_agent_is_the_provider_of_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    _validator_says(monkeypatch, "VALIDATION: PASS")

    loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs())

    assert gate_events(repo)[0].payload["provider"] in policy.ENGINE_GATE_PROVIDERS


def test_the_two_queue_sites_give_the_decision_kind_one_spelling() -> None:

    for site in (loop._hold_for_validate_decision, validate_gate.queue_unreadable_verdict):
        source = inspect.getsource(site)
        assert "decisions.enqueue" in source, "the probe must be reading a queueing site"
        assert "VALIDATE_DECISION_KIND" in source
        assert f'"{validate_gate.VALIDATE_DECISION_KIND}"' not in source

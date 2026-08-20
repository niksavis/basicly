"""What the VALIDATE advance records about the validator it paid for (basicly-xd79u3).

The defect these bind: a validator that executed, was charged for and returned no
readable verdict left the loop in exactly the state it was already in — no gate event,
no queue item, no rework — so the only surface that showed the run was the spend. One
measured advance dispatched a validator and two reviewers for $1.13 of one run alone and
moved nothing.

**Asserted against a real ledger, not a stubbed write seam.** A gate result is a ledger
event and `basicly tracker show` does not surface one, so a test that counted comments
could not see a gate either way — which is the wrong probe that nearly filed a different
bug. `flipped_tracker.flipped_repo` gives each test its own ledger and the count is taken
from it.

Three behaviours, and the third is what stops the fix being a lie: a verdict is recorded,
an unreadable verdict is queued for a human, and an advance that dispatched no validator
records nothing. Without the third a fail-silent becomes a fail-open, which is worse.
"""

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

# A record id the owned store accepts: the ledger validates ids, so the one-letter
# stand-in `test_loop` uses against a fake tracker is not a record here.
RECORD = "unit-1"

# A landed unit that owes the consumer gate: verify green, validate not yet recorded.
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
    """A checkout with the kit installed, an empty ledger, and record ``i`` seeded."""
    root = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(root, RECORD, title="a unit that owes the consumer gate")
    return root


@pytest.fixture(autouse=True)
def _validate_phase(monkeypatch: pytest.MonkeyPatch) -> None:
    """Pin the resume point and stub what the phase does *beside* the validator.

    The reviews are stubbed rather than run: they are advisory, they touch no gate, and
    `test_loop` already binds the fan-out. The tracker commit is stubbed because these
    ledgers are not git repositories.
    """
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
    """Make the dispatched validator exit 0 having said *reply* and nothing else."""
    monkeypatch.setattr(
        runner,
        "run",
        lambda spec, *_a, **_k: runner.RunResult(
            spec.name, tuple(spec.command), executed=True, returncode=0, stdout=reply
        ),
    )


def gate_events(repo: Path) -> list[Any]:
    """Every ``gate`` event the record carries in *repo*'s ledger, in file order.

    The probe the defect was measured with, in the one shape that can answer it: a
    ``passed`` payload on the record, counted before and after the advance.
    """
    kind = tracker.kit(repo).events.KIND_GATE
    return [
        event
        for event in flipped_tracker.ledger_events(repo)
        if event.kind == kind and event.record == RECORD
    ]


def test_a_validator_verdict_records_a_gate_event_on_the_ledger(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The gate the validator was dispatched to answer carries its answer afterwards.

    End to end from the reply text to the ledger event, because every step between the
    two is a place the verdict was thrown away: it is parsed off the agent's own words,
    written under the engine's provider (never the agent's — `br gate report`
    authenticates nothing), and only then does the phase move.
    """
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
    """An unreadable verdict is a fact an operator can dispose of; silence is not.

    The measured failure: this advance used to return having recorded nothing anywhere,
    so `loop status` reported the same gate and `loop decisions` the same empty queue as
    before it spent the dispatch. The reply rides on the item because it is the only
    copy — the run record carries usage, not text.
    """
    _validator_says(monkeypatch, "I could not launch the CLI and I am not sure why")

    result = loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs())

    queued = [item for item in decisions.items_by_id(repo, RECORD).values() if item.pending]
    assert len(queued) == 1
    assert queued[0].kind == "validate"
    assert "validator" in queued[0].question
    assert dispatch_brief.VERDICT_PREFIX in queued[0].question
    assert "could not launch the CLI" in queued[0].detail
    assert result.blocked and result.action == "decision" and result.to_phase == "validate"
    # And no verdict was invented for the gate the validator failed to answer.
    assert gate_events(repo) == []


def test_an_undispatched_validator_records_no_gate_event(repo: Path) -> None:
    """The fail-open guard: no dispatch, no gate event, no queue item.

    ``repair_dispatch=False`` is the supervisor's shape — a landing pass spawns no agent
    — so nothing ran here and there is no verdict to hold. A fix that recorded a gate on
    every advance out of VALIDATE would turn the fail-silent into a fail-open, which is
    strictly worse: the unit would land on a validation nobody performed.

    The zero is only evidence with a control, so the same probe is run again over a
    verdict written directly afterwards: it finds that one, which is what separates
    "nothing was recorded" from "the probe cannot see a gate".
    """
    result = loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs(), repair_dispatch=False)

    assert gate_events(repo) == []
    assert decisions.items_by_id(repo, RECORD) == {}
    assert result.blocked and result.needs_input == "validation"

    validate_gate.record_verdict(repo, RECORD, passed=True)

    assert len(gate_events(repo)) == 1, "the probe must be able to see a gate event"


def test_the_engine_and_not_the_agent_is_the_provider_of_record(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A required gate counts only the engine's own result (the jr0l.51 stance).

    The control on the first test's phase move: it advances because the re-read finds the
    gate *passed*, and `policy.gate_status` disregards any provider but the engine's — so
    if the verdict were reported under the agent's name the same reply would move nothing.
    """
    _validator_says(monkeypatch, "VALIDATION: PASS")

    loop.advance(repo, RECORD, config=CONFIG, inputs=loop.Inputs())

    assert gate_events(repo)[0].payload["provider"] in policy.ENGINE_GATE_PROVIDERS


def test_the_two_queue_sites_give_the_decision_kind_one_spelling() -> None:
    """Both sites that queue a validate decision name the symbol, never the string.

    `decision_marker.KINDS` reserves the kind, so a queue site and the reserved list
    only stay interchangeable while they agree - and a second spelling is the one a
    later reader copies. Read off the source text because the literal and the symbol
    evaluate to the same string, so nothing a call can observe discriminates them.
    """
    for site in (loop._hold_for_validate_decision, validate_gate.queue_unreadable_verdict):
        source = inspect.getsource(site)
        assert "decisions.enqueue" in source, "the probe must be reading a queueing site"
        assert "VALIDATE_DECISION_KIND" in source
        assert f'"{validate_gate.VALIDATE_DECISION_KIND}"' not in source

"""Answering `park` on a queued decision that offers it (basicly-vkjt).

A sibling of `test_cli_policy.py` rather than more of it: that module is frozen in
`[tool.module_size.frozen]` and may only shrink, so its fake and its escalation helper
are imported instead of copied. The `_isolate` fixture is redeclared because an autouse
fixture does not reach another module.

`test_cli_gate_verbs.py` already covered the gateless-escalation case and is what caught
an over-narrow first attempt here: binding on the full "retry, re-dispatch, or park?"
phrase rejected the four other wordings that offer the same route. What had no coverage
was the `stall` kind, which is where the defect lived.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import br, cli, decisions, policy
from tests.test_cli_policy import _escalate, _FakeBr


@pytest.fixture(autouse=True)
def _isolate(monkeypatch: pytest.MonkeyPatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    fake = _FakeBr()
    monkeypatch.setattr(policy, "_write", fake)
    monkeypatch.setattr(br, "run_br", fake)


def _stall(bound: str = "spend") -> decisions.DecisionItem:
    """Enqueue the stall the supervisor raises, spelled as its producer spells it."""
    return decisions.enqueue(
        Path(),
        "basicly-x",
        "stall",
        f"runner claude stopped on {bound}: retry, re-dispatch, or park?",
    )


def _spy_hold(monkeypatch: pytest.MonkeyPatch) -> dict:
    """Record what the carrier asks :func:`policy.hold_lane` to do."""
    held: dict = {}

    def _hold(_repo_root, issue_id, reason, gate=None):
        held.update(issue=issue_id, reason=reason, gate=gate)

    monkeypatch.setattr(policy, "hold_lane", _hold)
    return held


def test_answering_a_stall_with_park_actually_parks_the_lane(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The reported defect: the question offered `park` and the answer did nothing.

    Observed on `basicly-ca42#155136fefc` — it printed `answered <id> by human` and had
    no other effect, so the bead stayed dispatchable and the next pass would re-run it.
    """
    item = _stall()
    held = _spy_hold(monkeypatch)

    answer = "park - the owner keeps chars/4"
    assert cli.main(["loop", "answer", item.decision_id, answer, "--by", "niksa"]) == 0

    out = capsys.readouterr().out
    assert "parked basicly-x" in out
    assert policy.HELD_STATUS in out
    assert held["issue"] == "basicly-x"
    assert "chars/4" in held["reason"], "the operator's reason must reach the record"
    assert held["gate"] is None, "a stall names no gate, unlike a rework escalation"


def test_the_carrier_accepts_park_from_every_question_that_offers_it(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Bound to the producers, not to a list of kinds.

    Enumerated from the real producers rather than paraphrased: five wordings offer the
    route and only one carries a gate, so a carrier keyed on either the kind or the gate
    leaves the rest offering something that does nothing.
    """
    offered = (
        policy.rework_escalation_question("merge"),
        "runner claude stopped on spend: retry, re-dispatch, or park?",
        "the tracker's storage kept failing this dispatch: retry or park?",
        "dispatch failed at the rework cap: retry, re-dispatch, or park?",
        "two lanes keep colliding here: re-scope it, serialize it, or park?",
    )
    for question in offered:
        assert "or park?" in question.lower(), question

    held = _spy_hold(monkeypatch)
    escalation = _escalate()

    assert cli.main(["loop", "answer", escalation.decision_id, "park", "--by", "n"]) == 0

    assert held["gate"] == "merge", "an escalation names its gate and the record keeps it"
    assert "parked" in capsys.readouterr().out


def test_a_question_that_offers_no_routes_is_left_alone(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """The discriminating half: `park` is not a universal verb on the queue.

    A needs-input question offers no routes, so answering it `park` records the answer
    and holds nothing — without this, binding on the question would be no narrower than
    binding on nothing at all.
    """
    held = _spy_hold(monkeypatch)
    item = decisions.enqueue(Path(), "basicly-x", "needs-input", "which db?")

    assert cli.main(["loop", "answer", item.decision_id, "park", "--by", "niksa"]) == 0

    assert held == {}
    assert "parked" not in capsys.readouterr().out


def test_a_delegated_answer_cannot_park_a_stall(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """Widening the carrier must not widen who may use it.

    A deferred child leaves the open-child set, so a model that could park its own lane
    could drop a requirement and let the package close over the hole — the authority D15
    keeps human for Kill.
    """
    item = _stall()
    held = _spy_hold(monkeypatch)
    delegated = f"{decisions.DECIDER_BY_PREFIX}claude"

    assert cli.main(["loop", "answer", item.decision_id, "park", "--by", delegated]) == 0

    out = capsys.readouterr().out
    assert held == {}
    assert "a delegated answer does not park basicly-x" in out

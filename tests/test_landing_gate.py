"""Tests for what an answered gate escalation authorises (basicly-u2hl.54.3)."""

from __future__ import annotations

from typing import TYPE_CHECKING

from basicly import decisions, landing_gate, merge, policy

if TYPE_CHECKING:
    from pathlib import Path

    import pytest


def _item(
    *, question: str, answer: str | None, answered_by: str = "niksa"
) -> decisions.DecisionItem:
    """One queued item. ``answer=None`` is what makes it pending — there is no flag."""
    return decisions.DecisionItem(
        decision_id="d-1",
        issue_id="i",
        kind=policy.REWORK_ESCALATION_KIND,
        question=question,
        detail="",
        answer=answer,
        answered_by=answered_by,
        queued_at="2026-08-13T00:00:00Z",
    )


def _unreliable_question() -> str:
    """The wording the engine itself queues, so the parser under test recognises it."""
    return policy.unreliable_gate_escalation_question(merge.MERGE_GATE)


def _pin(monkeypatch: pytest.MonkeyPatch, items: list, *, spent: bool = False) -> None:
    monkeypatch.setattr(landing_gate.decisions, "items_on", lambda *_a, **_k: items)
    monkeypatch.setattr(landing_gate.policy, "gate_override_spent", lambda *_a: spent)


def test_an_answered_land_anyway_authorises_the_named_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """The gate name travels, so an answer about one gate cannot waive another."""
    _pin(monkeypatch, [_item(question=_unreliable_question(), answer="land anyway - it flakes")])
    assert landing_gate.gate_override(tmp_path, "i") == merge.MERGE_GATE


def test_a_spent_override_authorises_nothing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Once, not once per landing — the remedy is spent where it is used (basicly-tcmy.6)."""
    _pin(
        monkeypatch,
        [_item(question=_unreliable_question(), answer="land anyway")],
        spent=True,
    )
    assert landing_gate.gate_override(tmp_path, "i") is None


def test_a_delegated_answer_cannot_waive_a_landing_gate(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """An autonomy grant may dispose of the question; skipping a gate is not its call."""
    _pin(
        monkeypatch,
        [
            _item(
                question=_unreliable_question(),
                answer="land anyway",
                answered_by=f"{decisions.DECIDER_BY_PREFIX}claude",
            )
        ],
    )
    assert landing_gate.gate_override(tmp_path, "i") is None


def test_a_pending_escalation_is_not_an_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pending means unanswered; only a recorded answer authorises anything."""
    _pin(monkeypatch, [_item(question=_unreliable_question(), answer=None)])
    assert landing_gate.answered_unreliable_escalation(tmp_path, "i") is None


def test_an_unreadable_queue_reads_as_no_answer(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Fail closed: the answer this looks for is what permits skipping a gate."""

    def _raise(*_a, **_k):
        raise RuntimeError("queue unreadable")

    monkeypatch.setattr(landing_gate.decisions, "items_on", _raise)
    assert landing_gate.answered_unreliable_escalation(tmp_path, "i") is None

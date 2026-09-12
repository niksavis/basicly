from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

from . import decisions, merge, policy

if TYPE_CHECKING:
    from pathlib import Path


def answered_gate_escalation(
    repo_root: Path, issue_id: str, gate_from_question: Callable[[str], str | None]
) -> decisions.DecisionItem | None:

    try:
        items = decisions.items_on(repo_root, issue_id)
    except RuntimeError, ValueError, OSError:
        return None
    return next(
        (
            item
            for item in items
            if item.kind == policy.REWORK_ESCALATION_KIND
            and not item.pending
            and gate_from_question(item.question) is not None
        ),
        None,
    )


def answered_unreliable_escalation(repo_root: Path, issue_id: str) -> decisions.DecisionItem | None:
    return answered_gate_escalation(repo_root, issue_id, policy.gate_from_unreliable_escalation)


def answered_shared_gate_escalation(
    repo_root: Path, issue_id: str
) -> decisions.DecisionItem | None:
    return answered_gate_escalation(repo_root, issue_id, policy.gate_from_shared_gate_escalation)


def gate_override(repo_root: Path, issue_id: str) -> str | None:

    item = answered_unreliable_escalation(repo_root, issue_id)
    if item is None or not policy.answer_lands_anyway(item.answer or ""):
        return None
    if (item.answered_by or "").startswith(decisions.DECIDER_BY_PREFIX):
        return None
    gate = policy.gate_from_unreliable_escalation(item.question)
    if gate != merge.MERGE_GATE or policy.gate_override_spent(repo_root, issue_id, gate):
        return None
    return gate

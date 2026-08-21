"""What an answered gate escalation authorises (basicly-u2hl.54.3).

One responsibility: find the answer a human already gave to a gate escalation on
this node, and say what it permits. The boundary is *reading an answer* against
*acting on it* — :mod:`basicly.loop` spends the override, records the flake and
blocks the lane; nothing here writes.

Split out of ``loop`` when the size ratchet caught that module growing, along the
seam the four functions already had: each needs the bead id and the repo root and
nothing else the phase engine holds.
"""

from __future__ import annotations

# comment-density-waiver: cohesion: this module's payload IS provenance. Four small functions
# carrying the incident history that makes them correct - tcmy.6's unbounded escalation
# ladder, 4tjt's unimplemented remedy, why a delegated answer may not waive a gate, and
# why the gate name is returned rather than a bool. Cutting to 50% would delete the
# reasons and keep the code, which is the failure this gate exists to prevent.
from collections.abc import Callable
from typing import TYPE_CHECKING

from . import decisions, merge, policy

if TYPE_CHECKING:
    from pathlib import Path


def answered_gate_escalation(
    repo_root: Path, issue_id: str, gate_from_question: Callable[[str], str | None]
) -> decisions.DecisionItem | None:
    """The node's answered gate escalation of one wording, or None when there is none.

    Matched by handing each question back to the parser that owns the wording rather
    than by comparing against a reconstructed string — the same reason
    ``decisions.settle_checkpoint`` matches on content: a reworded ask must not
    silently stop being recognised, and an item queued under an earlier wording still
    resolves. *gate_from_question* is that parser, so each caller recognises only its
    own escalation and one wording's answer cannot dispose of another's.

    Any generation matches, deliberately: the ladder this ends was built out of
    generations, so "has this already been answered once" cannot be asked per
    generation.

    An unreadable queue reads as "no answer", the same stance
    ``decompose.bead_class_and_scope`` takes on this path — and the safe direction
    here, because the answer this looks for is what permits skipping a gate.
    """
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
    """The node's answered unreliable-gate escalation, or None when there is none."""
    return answered_gate_escalation(repo_root, issue_id, policy.gate_from_unreliable_escalation)


def answered_shared_gate_escalation(
    repo_root: Path, issue_id: str
) -> decisions.DecisionItem | None:
    """The node's answered shared-tracker-gate escalation, or None when there is none."""
    return answered_gate_escalation(repo_root, issue_id, policy.gate_from_shared_gate_escalation)


def gate_override(repo_root: Path, issue_id: str) -> str | None:
    """The gate an answered, unspent ``land anyway`` authorises skipping once, else None.

    The remedy the unreliable-gate escalation offers, carried out (basicly-tcmy.6).
    Answering used only to release the lane: the landing re-attempted, the same flaky
    gate tripped, and the identical question re-opened under the next generation — an
    unbounded ladder with the offered remedy unimplemented. This is basicly-4tjt's
    defect in the sibling escalation, and the shape of the fix is the one
    :func:`policy.grant_rework_allowance` gave that one — the answer is the decision,
    and the engine carries it out so the operator does not have to know that a second
    command exists.

    Read at the landing rather than when the answer is recorded, because the landing is
    where the authorisation is used: the override is then spent where it is spent, and
    it works whichever surface recorded the answer. This costs one comment scan on a
    path that is about to run a whole verify suite.

    A delegated answer does not override a gate, matching
    ``cli._carry_out_rework_retry``'s stance and for a stronger reason: an autonomy
    grant may dispose of the question, but skipping a landing gate is not something a
    model gets to authorise for itself. The other offered choice — fix the flake —
    stays open to it.

    Reporting the gate, not a bool, so the caller spends the override against the same
    gate name the answered question carried rather than a second guess at it — and so
    an answer about some *other* gate cannot waive this one. Only the landing gate is
    escalated this way today; reading the name is what keeps that from being an
    assumption a later enqueue site can break.
    """
    item = answered_unreliable_escalation(repo_root, issue_id)
    if item is None or not policy.answer_lands_anyway(item.answer or ""):
        return None
    if (item.answered_by or "").startswith(decisions.DECIDER_BY_PREFIX):
        return None
    gate = policy.gate_from_unreliable_escalation(item.question)
    if gate != merge.MERGE_GATE or policy.gate_override_spent(repo_root, issue_id, gate):
        return None
    return gate

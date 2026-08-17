"""The build entry predicate: may this *recorded* bead be dispatched into BUILD?

:mod:`basicly.plan_gate` judges a plan a decomposer proposed; this judges a bead the
tracker already holds, at the moment a lane is about to be dispatched. Same five
fields, different population and different evidence — the plan is a document in hand,
the bead is markup read back — which is the boundary this was split from the gate on
when that module crossed the size cap. Which fields those are is not respelled here:
:func:`plan_gate.missing_fields` answers for both halves, because two spellings of the
required set is exactly how they would come to require different things.

It exists for the units the decomposer never saw: a hand-filed bead carries no plan and
dispatching it spends the same tokens. It is a **pure read** that names the missing
field; the caller decides what to do with the verdict, exactly as
:func:`loop.stale_binding_verdict` splits the decision from the write.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from . import tracker
from .plan_gate import missing_fields
from .plan_record import PLAN_HEADING, has_heading, parse_plan_section


@dataclass(frozen=True)
class EntryVerdict:
    """Whether a unit may enter BUILD, and what it is missing if not."""

    issue_id: str
    missing: tuple[str, ...] = ()
    unreadable: bool = False

    @property
    def admitted(self) -> bool:
        """True only when every plan field is on the record."""
        return not self.missing and not self.unreadable

    @property
    def reason(self) -> str:
        """Why the unit was refused, naming each missing field; empty when admitted."""
        if self.unreadable:
            return (
                f"{self.issue_id} could not be read from the tracker, so the plan gate "
                "cannot say whether it carries a plan"
            )
        if not self.missing:
            return ""
        fields = ", ".join(self.missing)
        return (
            f"{self.issue_id} declares no {fields}; the plan gate refuses a lane BUILD "
            "cannot be held to, so declare the missing field before dispatching it"
        )


def entry_verdict_for(issue_id: str, description: str) -> EntryVerdict:
    """The build entry verdict for a bead whose body is already in hand (pure).

    The ratchet is the ``## Plan`` heading, not the fields under it. A body carrying
    the heading was written by the decomposer under this gate, so an incomplete one is
    a defect and is refused naming the field. A body with no heading at all predates
    the gate — the same population D8 refuses to bulk-transform — and is admitted,
    because a predicate that refuses every bead filed before it existed does not gate
    the work that comes after it, it stops the harness.

    That ratchet is per-heading, not per-field, which is why the sixth plan field
    (:data:`plan_gate.DEMONSTRATION_FIELD`, D18) is not checked here: every bead
    recorded under the heading before that field existed carries no demonstration line,
    so on this population its absence is ambiguous between a defect and predating the
    rule. :func:`plan_gate.gate_plan` requires it, and has no such population.
    """
    if not has_heading(description, PLAN_HEADING):
        return EntryVerdict(issue_id)
    recorded = parse_plan_section(description)
    return EntryVerdict(issue_id, missing_fields(recorded))


def build_entry_verdict(repo_root: Path, issue_id: str) -> EntryVerdict:
    """Whether *issue_id* may be dispatched into BUILD (a read; the caller acts).

    Fail-closed on an unreadable record: a tracker that did not answer is not a bead
    that declared a plan, and admitting one on a transient read failure is how a gate
    that exists stops binding.
    """
    record = tracker.read_record(repo_root, issue_id)
    if not isinstance(record, dict):
        return EntryVerdict(issue_id, unreadable=True)
    description = record.get("description")
    if not isinstance(description, str):
        return EntryVerdict(issue_id, unreadable=True)
    return entry_verdict_for(issue_id, description)

"""BUILD's second entry predicate: the bound on work standing downstream of build.

Requirements 3.1 states BUILD's entry predicate as **plan gate green and downstream
WIP below limit**, and until this module nothing implemented the second half
(basicly-u2hl.23). ``[worktree] concurrency`` was the only bound in the engine and it
answers a different question: it caps how many lanes run **at once**, while this caps
how much **unlanded** work exists. A pass can sit comfortably inside the first and
exhaust the second — five lanes landing faster than anyone reviews them is five
lanes' worth of unreviewed surface and of merge conflicts waiting to happen, and
neither the concurrency cap nor D3's spend ceiling can see it. The quantity that
actually runs out is review capacity, and it is denominated in units, not tokens.

**Downstream is the parked phases**, :data:`DOWNSTREAM_PHASES` — merged and waiting in
verify, owing a consumer check in validate, or waiting on a ship checkpoint. That set is
what ``supervise.advance_parked`` imports and drives each pass: what drains the bound is
exactly what the bound counts, so a blocked pass is not a wedged one. A closed unit is
done and counts for nothing; a unit still building has produced nothing to review yet.

**The pass's own admissions do not count toward the limit** (basicly-08rnmd). This
is a gate on what already stands downstream, not a per-pass quota: below the limit
every ready lane starts and ``[worktree] concurrency`` decides how many of them run
at once, at or above it none starts and all are refused naming the limit. Charging a
pass for its own cohort idled half a ten-slot pass while the review queue was empty
— the cost the bound exists to prevent, not one for it to impose.

This module knows nothing of lanes, worktrees or runners: it works over anything
carrying an ``issue_id`` (:class:`Unit`) and hands the caller back its own objects, so
it sits *below* :mod:`basicly.supervise` and the dependency runs one way only.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Protocol

from . import decisions, loop_state
from .config import load_policy_config

# The loop phases a unit occupies after BUILD has produced its work and before it is
# done: each has merged and is consuming review capacity. `intake`/`classify`/
# `decompose`/`build` are upstream and `done` has landed, so neither counts.
DOWNSTREAM_PHASES = ("verify", "validate", "ship")

# What a human is being asked when the bound admits nothing at all. Phrased as the two
# things that actually clear it, because "WIP limit reached" tells an operator the
# state and not the move.
WIP_QUESTION = (
    "review and land the finished lanes, or raise [policy] max_downstream_wip: this "
    "session's unlanded work is at its limit and no further lane can start"
)


class Unit(Protocol):
    """What the bound counts: anything a ``br`` issue id can be read off.

    A structural type rather than an import of ``supervise.AdoptedLane``, which is
    what lets the caller get its own lanes back instead of ids it has to look up
    again — and what keeps this module below the supervisor rather than in a cycle
    with it.
    """

    @property
    def issue_id(self) -> str:
        """The ``br`` issue this unit is the work for."""
        ...


@dataclass(frozen=True)
class WipAdmission[T: Unit]:
    """How much unlanded work stands downstream of build, and what it admits."""

    limit: int
    # Ids of the units already past build and not yet done, in the order read.
    downstream: tuple[str, ...]
    # The ready lanes this bound lets start, and the ones it holds — both in the
    # dispatch order they were offered in, so a refusal never reorders the queue.
    admitted: tuple[T, ...]
    refused: tuple[T, ...]

    @property
    def stalled(self) -> bool:
        """True when the bound holds every ready lane, so the pass starts nothing."""
        return bool(self.refused) and not self.admitted

    @property
    def reason(self) -> str:
        """Why a held lane did not start, naming the limit it is held by.

        The count, not the ids: which units to go and land is the *caller's* half of
        the message (``supervise.dispatch_lanes`` appends it from :attr:`downstream`),
        so a caller that renders the refusal differently is not made to strip a list
        back out of a sentence.
        """
        return (
            f"downstream work in progress is at the [policy] max_downstream_wip limit "
            f"of {self.limit} ({len(self.downstream)} unit(s) past build)"
        )

    @property
    def coverage(self) -> str:
        """What the bound saw and what it did — reported whether or not it refused.

        Printed on the admitted path too, deliberately, on the rule the pass's other
        cost gates already follow (``supervise._report_coverage``): the failure this
        whole module closes was a bound that did not exist, and an unbounded pass must
        never again be indistinguishable at the surface from a checked one.
        """
        parts = [f"{len(self.downstream)}/{self.limit} unlanded downstream of build"]
        if self.downstream:
            parts.append(f"waiting: {', '.join(self.downstream)}")
        parts.append(f"{len(self.admitted)} lane(s) admitted")
        if self.refused:
            parts.append(f"REFUSED: {self._held_ids()}")
        return "; ".join(parts)

    @property
    def detail(self) -> str:
        """The refusal spelled out for the queue item, or "" when nothing was held."""
        if not self.refused:
            return ""
        return (
            f"{len(self.refused)} ready lane(s) not dispatched ({self._held_ids()}): {self.reason}"
        )

    def _held_ids(self) -> str:
        """``a.4, a.5`` — the lanes this bound refused."""
        return ", ".join(unit.issue_id for unit in self.refused)


def downstream_units(repo_root: Path, issue_ids: Iterable[str]) -> tuple[str, ...]:
    """Those of *issue_ids* whose derived loop phase is downstream of build.

    Phase is derived from ``br`` (:func:`loop_state.read_node_state`), never from a
    side record: the supervisor keeps no state, so the count has to be recoverable by
    a successor that has only the tracker.
    """
    return tuple(
        issue_id
        for issue_id in issue_ids
        if loop_state.read_node_state(repo_root, issue_id).phase in DOWNSTREAM_PHASES
    )


def admit[T: Unit](
    repo_root: Path, ready: Sequence[T], parked: Iterable[T], *, exclude: str = ""
) -> WipAdmission[T]:
    """Split *ready* into the lanes the WIP bound lets start and the ones it holds.

    *ready* is the pass's dispatch-ordered lanes and *parked* the session's live
    units, the ones a phase read might find downstream; *exclude* drops one id from
    that count, which is how a session's anchoring root stays out of a tally of the
    work it is the parent of. The split is all-or-nothing — the count that decides it
    is of work already downstream — and dispatch order is preserved either way.

    The limit is read here rather than passed in so no dispatch path can bypass the
    bound by forgetting to look it up, which is the same rule ``dispatch_lanes``
    applies to D3's spend ceiling.
    """
    limit = load_policy_config(repo_root).max_downstream_wip
    downstream = downstream_units(
        repo_root, (unit.issue_id for unit in parked if unit.issue_id != exclude)
    )
    at_limit = len(downstream) >= limit
    return WipAdmission(
        limit=limit,
        downstream=downstream,
        admitted=() if at_limit else tuple(ready),
        refused=tuple(ready) if at_limit else (),
    )


def record_refusal[T: Unit](
    repo_root: Path, root_issue: str, admission: WipAdmission[T]
) -> decisions.DecisionItem | None:
    """Queue the refusal on the root when the bound admits nothing, else do nothing.

    Only when the pass starts *nothing*: a pass that dispatched some of its lanes has
    visibly done work and the held ones follow on a later pass with no human involved,
    so queuing there would page an operator about a bound working as designed. A pass
    that starts nothing is the one an attached client would otherwise read as "no ready
    lanes", which is the silent shape ``record_dispatch_halt`` exists to prevent.

    Idempotent per (issue, kind, question), so a session that keeps refusing re-enqueues
    the one item; the counts live in the detail, which is not part of the id.
    """
    if not admission.stalled:
        return None
    return decisions.enqueue(repo_root, root_issue, "escalation", WIP_QUESTION, admission.detail)

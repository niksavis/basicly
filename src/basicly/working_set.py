"""The working-set band at dispatch: does this lane's estimated size admit a run?

D8's sizing governor refuses an out-of-band *plan* at decompose; this is the same band
applied one stage later, at the moment a lane is handed to a runner (basicly-jr0l.16).
It lives apart from :mod:`basicly.supervise` because it asks about **one package's
size**, answerable from the tracker and the sizing config alone — no lock, no session,
no runner — and three callers outside the supervisor ask it: the lane mini-loop in
:mod:`basicly.loop`, the CLI's band report, and the contention preflight.

The rest of this module's reasoning is the commentary below, carried over verbatim from
where the band used to live.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from . import decisions, decompose, policy

if TYPE_CHECKING:
    from .config import SizingConfig

#
# The sizing governor refuses an out-of-band *plan* at decompose, and nothing used to
# check the band again at dispatch — so it bound only work that arrived through
# decompose, while a supervised pass over pre-existing leaf beads started whatever the
# scheduler ranked first at any size. Re-measured on this repo's own ready set with the
# band at 8000..64000, the top-ranked lane estimates 108605 working-set tokens — 70% over
# the ceiling a plan would have been refused for — and three siblings are over it too. So
# the check belongs here, before anything spawns, where ``runner.run`` refuses a dispatch
# whose model tier resolves to nothing (basicly-kjc5.59): cost is bounded by sizing the
# work, never by killing a working agent.
#
# The two ends of the band deliberately earn different severities:
#
# - **Above the ceiling the dispatch is refused.** The run would overflow the very
#   window it was sized against, so the tokens buy a partial answer at best. The remedy
#   is a decompose action no engine can take, so the lane is held by a *pending* queue
#   item until a human takes it.
# - **Below the floor the dispatch is escalated and then proceeds.** An under-size lane
#   succeeds; it merely wastes the per-lane overhead a merge with a sibling would have
#   saved. Holding it would strand deliverable work behind a human answer over an
#   economic inefficiency, and a ready set of small beads would wedge a whole supervised
#   run — the expensive failure in the cheap direction.
#
# A lane whose scope cannot be read at all is **admitted**, which is not the same
# indeterminate answer as an estimate outside the band: 60 of this repo's 87 open beads
# carry no ``## Scope`` — including basicly-jr0l.16 itself — so failing closed on a
# missing estimate would turn a sizing governor into a ban on hand-filed work. The model
# precedent refuses because a *declared* tier resolved to nothing; an absent scope
# declares nothing to contradict.
#
# It is admitted **visibly**, though (basicly-jr0l.60): nothing used to distinguish a
# lane the band had checked and passed from one it had never looked at, in the
# admission, the queue or the pass output. Measured on the 2026-08-01 four-lane proof
# run, every dispatched lane was unsizeable, so the band checked *nothing* and reported
# what it reports when everything fits — and all four overran. So:
#
# * The **undeclared** case (the bead read fine and declares no scope) is a fact about
#   the bead, and it is escalated: recorded on the lane and named in the pass output,
#   then retired by the engine so the lane still dispatches. Same disposal as the
#   under-floor advisory, for the same reason — holding the majority of a real tracker
#   behind a human answer is the ban failing closed would have been. It is charged to
#   the engine rather than notified for the same reason: a notification per hand-filed
#   lane is a ban by noise.
# * The **unreadable** case (the read itself failed) stays genuinely indeterminate and
#   is not escalated. A transient br failure is not a finding about the package, and
#   queuing one would put a tracker hiccup in the audit trail as a sizing defect.


# The queue question an out-of-band lane asks, named once because
# :func:`decisions.enqueue` keys items by (issue, kind, question) — a second copy of
# this string would leak a pending item that nothing can find (basicly-jr0l.52).
# The numbers stay in the *detail*, which is not part of the id, so re-deriving the
# same pass finds the item it already queued instead of a fresh generation of it.
# Every remedy it names changes the check's own inputs, deliberately: this is a
# deterministic gate, so an answer releases the lane but never overrides the
# arithmetic — a lane answered without a re-scope refuses again on its next pass.
SIZING_QUESTION = "working set is outside the configured band: re-scope it or widen the band?"

# The queue question a never-checked lane asks — a distinct string, so an unsized lane
# and an out-of-band one are separate items rather than two generations of one, and an
# operator reading the queue can tell "too big" from "never measured".
UNSIZED_QUESTION = "working set was never checked: declare this package's scope?"

# How the engine retires each advisory it does not hold the lane for. The wording is
# the disposal reasoning, recorded where the decision is (D11).
_UNDERSIZE_DISPOSAL = (
    "dispatched anyway: under-cutting the floor wastes per-lane overhead, "
    "but the package is deliverable and holding it would strand it"
)
_UNSIZED_DISPOSAL = (
    "dispatched anyway: an undeclared scope is the normal state of a hand-filed bead, "
    "so holding it would ban hand-filed work rather than size it — recorded so this "
    "dispatch is not mistaken for one the band checked"
)

# The other way out of a ceiling refusal, offered only when the estimate priced a
# ``## Scope``. `check_working_set` says "split it", which is right for a lane that is
# genuinely that large and wrong for one whose number grew because the merge gate
# obliged its author to name more ground (basicly-efw2). Which declaration was priced
# is this module's question, so the alternative is worded here rather than in `policy`.
_DECLARE_WORKING_SET = (
    " — or, if completing that declaration for the merge collision gate is what made it "
    "large, declare the subset the lane must actually read under a `## Working Set` "
    "heading, which the band prices instead"
)


@dataclass(frozen=True)
class WorkingSetAdmission:
    """Whether a lane's estimated working set admits a dispatch (D8 at dispatch).

    *violation* is :func:`policy.check_working_set`'s own guidance message, so the
    band rule and its wording stay in one place; *refused* only classifies which end
    of the band was crossed, which is what decides severity.
    """

    issue_id: str
    # None when the bead declares no ``## Scope`` or could not be read at all.
    sizing: decompose.DispatchSizing | None
    violation: str | None
    refused: bool
    # Which absence left this lane unsized: ``decompose.SCOPE_UNDECLARED`` or
    # ``decompose.SCOPE_UNREADABLE``. Empty when *sizing* is there — so
    # :attr:`checked` reads off the estimate, not off this.
    absence: str = ""

    def record_inputs(self, repo_root: Path) -> dict[str, object]:
        """The dispatch record's sizing keywords; empty when nothing was estimable.

        Takes *repo_root* because one of those keywords is the forecast **spend**,
        which is resolved from this repo's calibration (basicly-tcmy.34).
        """
        return {} if self.sizing is None else self.sizing.record_inputs(repo_root)

    @property
    def checked(self) -> bool:
        """True when a real estimate was actually compared against the band.

        The distinction the surface used to lack (basicly-jr0l.60): ``violation is
        None`` answers "nothing was wrong", which is also what a lane nobody measured
        looks like.
        """
        return self.sizing is not None


def admit_working_set(repo_root: Path, issue_id: str, sizing: SizingConfig) -> WorkingSetAdmission:
    """Estimate *issue_id*'s working set and judge it against the band (pure read).

    Reuses :func:`decompose.resolve_dispatch_sizing` — the same estimator whose
    forecast the dispatch record already carries (basicly-jr0l.34) — so the number
    that gates a dispatch and the number recorded beside its actual cannot disagree.
    That also means no new tracker read: this *is* the read the dispatch already made,
    moved ahead of the bundle.

    Never raises. Neither absence refuses, on the reasoning in this section's header,
    but they are not the same admission: an undeclared scope carries the never-checked
    notice that :func:`escalate_working_set` records, while a failed read carries
    nothing, because it is a fact about the tracker rather than about the package.
    """
    lookup = decompose.SizingLookup(None, decompose.SCOPE_UNREADABLE)
    with contextlib.suppress(RuntimeError, ValueError, OSError):
        lookup = decompose.resolve_dispatch_sizing(repo_root, issue_id)
    resolved = lookup.sizing
    if resolved is None:
        # Greenfield counts with undeclared, not with unreadable (basicly-jr0l.69):
        # both are structural facts about the package that re-reading will not change,
        # and both leave the working set genuinely unknown. Only a failed tracker read
        # stays silent, because that is a fact about the tracker.
        unchecked = (
            policy.unchecked_working_set(issue_id, sizing)
            if lookup.absence in (decompose.SCOPE_UNDECLARED, decompose.SCOPE_GREENFIELD)
            else None
        )
        return WorkingSetAdmission(issue_id, None, unchecked, refused=False, absence=lookup.absence)
    estimate = resolved.estimate
    violation = policy.check_working_set(issue_id, estimate.total, estimate.scope_tokens, sizing)
    # `check_working_set` still decides *whether* the band was crossed; this comparison
    # only says which end, because only the ceiling refuses.
    refused = violation is not None and estimate.total > sizing.working_set_max
    if refused and resolved.working_set_source == decompose.WORKING_SET_FROM_SCOPE:
        violation = f"{violation}{_DECLARE_WORKING_SET}"
    return WorkingSetAdmission(issue_id, resolved, violation, refused=refused)


def escalate_working_set(
    repo_root: Path, admission: WorkingSetAdmission
) -> decisions.DecisionItem | None:
    """Queue an out-of-band or never-checked lane's sizing verdict; None when it fits.

    A refusal stays **pending**, and that is what holds the lane: ``ready_lanes``
    drops a lane with an unanswered item, so the package is not dispatched again
    until someone re-scopes it. Every other advisory — under the floor, or never
    checked at all because the bead declares no scope — is retired by the engine in
    the same breath: recorded for the audit trail, charged to the delegated column
    rather than a human's wait (D11), and out of ``has_pending`` — the same disposal
    :func:`resolve_stall_flag` makes of its own moot question.

    None also for a lane whose read failed: ``admit_working_set`` leaves that
    violation None precisely so a tracker hiccup is not filed as a sizing finding.
    """
    if admission.violation is None:
        return None
    unsized = admission.absence in (decompose.SCOPE_UNDECLARED, decompose.SCOPE_GREENFIELD)
    item = decisions.enqueue(
        repo_root,
        admission.issue_id,
        "escalation",
        UNSIZED_QUESTION if unsized else SIZING_QUESTION,
        admission.violation,
        human_required=admission.refused,
    )
    if not admission.refused:
        decisions.answer(
            repo_root,
            item.decision_id,
            _UNSIZED_DISPOSAL if unsized else _UNDERSIZE_DISPOSAL,
            by=decisions.ENGINE_BY,
        )
    return item


def band_coverage(working_sets: tuple[WorkingSetAdmission, ...]) -> str:
    """Which of this pass's lanes the band actually measured, and which it could not.

    Reported on every pass for the same reason :attr:`PassSpendAdmission.coverage` is:
    the failure this closes was silent. A pass of lanes the band never looked at
    printed nothing at all, so it was indistinguishable at the surface from a pass
    where every estimate fitted — and on the run that measured it, all four lanes were
    the former and all four overran (basicly-jr0l.60).
    """
    if not working_sets:
        return "no lanes to check"
    by_absence: dict[str, list[str]] = {}
    checked: list[str] = []
    for item in working_sets:
        if item.checked:
            checked.append(item.issue_id)
        else:
            by_absence.setdefault(item.absence, []).append(item.issue_id)
    parts = []
    if checked:
        parts.append(f"checked: {', '.join(checked)}")
    for absence, ids in sorted(by_absence.items()):
        parts.append(f"NEVER CHECKED ({absence}): {', '.join(ids)}")
    return "; ".join(parts)


def band_report(working_sets: tuple[WorkingSetAdmission, ...]) -> tuple[str, ...]:
    """One line per candidate: its working-set estimate and what the band would do.

    :func:`band_coverage` answers "did the band look?" in a single line, which is the
    right shape *during* a pass. Before one, the operator is deciding whether to mint a
    budget at all, and that decision needs the per-lane numbers — an aggregate forecast
    built from the unsizeable-lane assumption reads exactly like a measurement of lanes
    nobody measured (basicly-prnm, the same silent shape as basicly-jr0l.60).

    Ordered largest estimate first, then the unsized: the big lanes decide the budget,
    and the absent ones are the authoring fix that changes the budget most.
    """
    sized = sorted(
        (w for w in working_sets if w.sizing is not None),
        key=lambda w: w.sizing.estimate.total if w.sizing else 0,
        reverse=True,
    )
    lines = [
        f"  {w.issue_id:<22} {w.sizing.estimate.total:>9} tok  {_band_verdict(w)}"
        for w in sized
        if w.sizing is not None
    ]
    # Named, never folded into a count: an undeclared scope is an authoring fix that
    # takes a minute, and it is the largest single lever on what a pass costs.
    lines += [
        f"  {w.issue_id:<22} {'unsized':>9}      no scope the estimator can read"
        for w in working_sets
        if w.sizing is None
    ]
    return tuple(lines)


def _band_verdict(admission: WorkingSetAdmission) -> str:
    """What the band would do with one sized lane, worded for the pre-run table.

    The floor is deliberately skipped when a scope matches nothing on disk, so a
    greenfield package is not refused for having nothing to read yet
    (:func:`policy.check_working_set`). That leaves a *broken* glob indistinguishable
    from a greenfield one at the surface: both estimate to bare overhead and both read
    as a comfortably small "in band" lane. Measured on this repo's own tracker, four
    candidates sat at exactly the overhead figure. Say it instead, because the fix
    differs — one needs a corrected path, the other needs nothing (the gate that
    refuses an empty glob outright is basicly-a3ab.3).
    """
    if admission.refused:
        return "REFUSED - too large, split it"
    if admission.violation is not None:
        # Only the ceiling refuses (see :func:`admit_working_set`), so a lane under the
        # floor still dispatches while carrying the band's advice. Printing a bare
        # "in band" for it would report the opposite of what the band actually said.
        return "under the floor - dispatches, but merge it with a sibling"
    if admission.sizing is not None and admission.sizing.estimate.scope_tokens == 0:
        return "in band, but its scope matched no file"
    return "in band"

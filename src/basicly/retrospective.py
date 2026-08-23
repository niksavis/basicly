"""The special-cause signal over the gate-failure ledger, and what a retrospective owes.

RETROSPECTIVE is not a lane state and nothing here is a rung in the ladder: no unit ever
sits in it, ``config.LOOP_PHASES`` does not carry it and ``loop._HANDLERS`` has no entry
for it. architecture.md §26.3 makes it a *conditional process* over the recorded
gate-failure history, entered by a computed signal — so the module answers one question.
Does this ledger show a special cause, or a stable process read one failure at a time?

**Suppression is the reason it exists.** Every other mechanism in the harness decides to
do work; this is the first that decides not to. A single failure inside the limits is
common cause, and acting on one is *tampering*, which Deming's funnel experiment shows
"invariably increases variation in the results of a stable process". A retrospective
that runs on every failure is worse than none, so :data:`MIN_OBSERVATIONS` and
:data:`MIN_SPECIAL_COUNT` are load-bearing rules rather than defensive padding.

The chart is a c-chart: each point counts a rare event on one unit, so the process is
Poisson and sigma is ``sqrt(c-bar)``. Special cause is then the NIST/SEMATECH e-Handbook
§pmc31 definition — a point beyond three sigma, or a non-random run or trend inside the
limits. The output contract is deliberately **not** the why-chain (§3.2): a named control that
would have refused the defect, its tier, and the class of defects it covers. Card
(*BMJ Quality & Safety*, 2017) is why a chain alone is refused — iterated why yields one
causal path, chosen by the asker, and does not reproduce between analysts — so an
outcome carrying a chain must carry the branch it did not take beside it.
"""

from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import policy, tracker

# The marker family this module owns, a sibling of `[harness-policy]` and
# `[harness-review]`, carried through the `tracker.add_comment` seam they share.
MARKER = "[harness-retro]"

# The phase a retrospective dispatch resolves its role and records its cost under: it is
# `roles.ROLE_BY_PHASE`'s key for `retrospector`, and deliberately absent from
# `config.LOOP_PHASES` and `loop_state.PHASES`, which are the ladder. Priced as the read
# it is, because `dispatch_phase.WRITE_PHASES` is a closed set.
PHASE = "retrospective"

# The 3 in "a point beyond three sigma" (§3.2).
_SIGMA_MULTIPLE = 3

# The shortest ledger any rule can fire on: the run rule needs seven consecutive points,
# and a mean over fewer is dominated by the very point being tested. NIST asks for 20-25
# subgroups before limits are trusted, so this is the floor below which the question is
# not askable rather than a claim that seven is enough to be confident.
MIN_OBSERVATIONS = 7

# One failure is never a special cause, whatever the arithmetic says. On a ledger this
# sparse the c-chart's normal approximation breaks down: at nineteen clean units and one
# failure c-bar is 0.05 and the three-sigma limit lands at 0.72, so every isolated
# failure would signal. The exact Poisson tail refutes that — P(X >= 1) there is 4.9%,
# nowhere near the 0.135% a three-sigma tail admits — and firing on it is exactly the
# tampering §3.2 forbids.
MIN_SPECIAL_COUNT = 2

# Consecutive points above the centre line that count as a non-random run, and
# consecutive rising points that count as a trend: the run-of-seven and trend-of-six of
# the standard Shewhart run tests.
RUN_LENGTH = 7
TREND_LENGTH = 6

# The three rules, named so a fired signal says which one saw it.
BEYOND_LIMITS = "beyond-limits"
RUN = "run"
TREND = "trend"

# The output's tiers, strongest first (§3.2). `documentation` is a downgrade and owes
# the reason no stronger control was available.
CONTROL_TIER = "control"
WARNING_TIER = "warning"
DOCUMENTATION_TIER = "documentation"
TIERS = (CONTROL_TIER, WARNING_TIER, DOCUMENTATION_TIER)


@dataclass(frozen=True)
class Point:
    """One observation: a unit of the session, and the gate failures recorded on it."""

    issue_id: str
    failures: int


@dataclass(frozen=True)
class Chart:
    """The c-chart a ledger's points are judged against."""

    centre: float
    sigma: float
    upper: float
    observations: int


@dataclass(frozen=True)
class Signal:
    """Whether a ledger shows a special cause, and the inputs that decided it.

    The chart travels with the verdict: "this was a special cause" is checkable only
    beside the limit that was crossed, and "nothing fired" only beside the limit nothing
    reached. Rendering it is the reader's job.
    """

    fires: bool
    chart: Chart
    rule: str = ""
    point: str = ""
    detail: str = ""


def read_ledger(repo_root: Path, root_issue: str) -> tuple[Point, ...]:
    """The gate-failure ledger of *root_issue*'s session, one point per unit.

    A point counts rework markers, because a rework attempt is recorded exactly when a
    gate refused that unit's work. The neighbouring families are deliberately excluded:
    ``gate-unreliable`` is a defect in the gate rather than in the work, and
    ``gate-shared-tracker`` is a failure another lane's record caused. Counting either
    blames a unit that did not produce the failure, which is the one thing the chart has
    to get right.
    """
    return tuple(
        Point(issue_id, policy.rework_recorded(repo_root, issue_id))
        for issue_id in policy.session_issue_ids(repo_root, root_issue)
    )


def chart(points: Sequence[Point]) -> Chart:
    """The c-chart for *points* (pure).

    Sigma is ``sqrt(c-bar)`` and not a sample deviation: the points count a rare event
    over equal opportunity, so the process is Poisson and its variance *is* its mean. A
    sample deviation, taken over the same handful of points, widens with the outlier it
    is supposed to detect.
    """
    counts = [point.failures for point in points]
    centre = sum(counts) / len(counts) if counts else 0.0
    sigma = math.sqrt(centre)
    return Chart(centre, sigma, centre + _SIGMA_MULTIPLE * sigma, len(counts))


def evaluate(points: Sequence[Point]) -> Signal:
    """Whether *points* show a special cause, and which rule saw it (pure).

    Rules are tried strongest first and the answer names one, not a list: the dispatch
    it fires is one retrospective about one assignable cause. Silence is the expected
    answer, and both suppressions come before any rule.
    """
    limits = chart(points)
    if limits.observations < MIN_OBSERVATIONS:
        return Signal(
            False,
            limits,
            detail=(
                f"the ledger holds {limits.observations} observation(s), below the "
                f"{MIN_OBSERVATIONS} the shortest rule can fire on"
            ),
        )
    for rule in (_beyond_limits, _run_above_centre, _rising_trend):
        signal = rule(points, limits)
        if signal is not None:
            return signal
    return Signal(
        False,
        limits,
        detail="common cause: no point beyond the limits, and no run or trend inside them",
    )


def _beyond_limits(points: Sequence[Point], limits: Chart) -> Signal | None:
    """The latest point above the upper control limit, or None (pure).

    The latest rather than the first: :func:`claim` keys on the named point, so naming
    the earliest would suppress every later special cause behind the first.
    """
    for point in reversed(points):
        if point.failures > limits.upper and point.failures >= MIN_SPECIAL_COUNT:
            return Signal(
                True,
                limits,
                BEYOND_LIMITS,
                point.issue_id,
                f"{point.issue_id} carries {point.failures} gate failures, beyond the "
                f"upper control limit: {point.failures} failures on one unit is one "
                f"special cause, not {point.failures} common ones",
            )
    return None


def _run_above_centre(points: Sequence[Point], limits: Chart) -> Signal | None:
    """The latest window of :data:`RUN_LENGTH` points above the centre line (pure).

    Above only. A run *below* the centre is a real Shewhart signal, but this mechanism's
    output is a control that would have refused a defect, and a stretch of
    cleaner-than-usual units has no such output to give.
    """
    end = _last_window(
        [point.failures for point in points],
        RUN_LENGTH,
        lambda window: all(count > limits.centre for count in window),
    )
    if end is None:
        return None
    return Signal(
        True,
        limits,
        RUN,
        points[end].issue_id,
        f"a non-random run: {RUN_LENGTH} consecutive units through {points[end].issue_id} "
        f"sit above the centre line, inside the limits",
    )


def _rising_trend(points: Sequence[Point], limits: Chart) -> Signal | None:
    """The latest window of :data:`TREND_LENGTH` strictly rising points (pure).

    Rising only, for the reason :func:`_run_above_centre` is one-sided.
    """
    end = _last_window(
        [point.failures for point in points],
        TREND_LENGTH,
        lambda window: all(b > a for a, b in itertools.pairwise(window)),
    )
    if end is None:
        return None
    return Signal(
        True,
        limits,
        TREND,
        points[end].issue_id,
        f"a non-random trend: {TREND_LENGTH} consecutive units through "
        f"{points[end].issue_id} rise, inside the limits",
    )


def _last_window(
    counts: Sequence[int], length: int, accepts: Callable[[Sequence[int]], bool]
) -> int | None:
    """The end index of the latest window of *length* counts *accepts* takes (pure).

    ``accepts`` and not ``holds``: ``holds`` is a live field on
    ``worktree.RemovalVerdict``, and the wired-or-deleted gate matches a bare name, so
    the word here retires a real suppression (basicly-jr0l.70, basicly-r343).
    """
    for end in range(len(counts), length - 1, -1):
        if accepts(counts[end - length : end]):
            return end - 1
    return None


def _fired_marker(signal: Signal) -> str:
    """The once-only marker this signal is claimed with (pure)."""
    return f"{MARKER} fired rule={signal.rule} point={signal.point}"


def claim(repo_root: Path, root_issue: str, signal: Signal) -> bool:
    """Claim *signal* for one retrospective; False when it is already claimed.

    Keyed on the rule and the point, so one assignable cause fires once however many
    further failures land under it; an unkeyed marker would either re-dispatch on every
    later gate failure or swallow the next, different special cause. Written before the
    dispatch, because the failure to avoid is a repeated paid run on a signal nothing
    changed, and a store that cannot answer refuses the claim.
    """
    marker = _fired_marker(signal)
    if any(
        _first_line(str(comment.get("text", ""))) == marker
        for comment in tracker.try_read_comments(repo_root, root_issue)
    ):
        return False
    return tracker.try_add_comment(repo_root, root_issue, f"{marker}\n{signal.detail}")


def _first_line(text: str) -> str:
    """*text*'s first line, stripped — token-exact matching, like ``policy`` (pure)."""
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


# The fields a reply may state, by the wire names it states them under. No record:
# `settle` is the only consumer, and one whose fields nothing outside this module reads
# is the instrument-wired-to-nothing shape `wired_or_deleted` catches. It earns a type
# when the D25 consumer that holds one is built.
FIELDS = ("control", "tier", "defect-class", "downgrade-reason", "chain", "branch-not-taken")


def prompt(root_issue: str, signal: Signal) -> str:
    """The retrospector's brief: the signal, its inputs, and the contract it owes (pure)."""
    return "\n".join((
        f"A special-cause signal fired on the gate-failure ledger of {root_issue}.",
        f"rule: {signal.rule}",
        f"point: {signal.point}",
        f"chart: centre {signal.chart.centre:.2f}, sigma {signal.chart.sigma:.2f}, upper "
        f"limit {signal.chart.upper:.2f}, over {signal.chart.observations} observations",
        f"detail: {signal.detail}",
        "",
        "Answer with these fields, one per line, and nothing else:",
        "  control: the named control that would have refused this defect",
        f"  tier: one of {', '.join(TIERS)}",
        "  defect-class: the class of defects that control covers",
        "  downgrade-reason: at the documentation tier, why nothing stronger was available",
        "  chain: the causal path you followed, if you followed one",
        "  branch-not-taken: the branch you did not follow (required with a chain)",
        "",
        "A why-chain alone is not an answer: iterated why yields one path, chosen by the",
        "asker, and does not reproduce between analysts (Card 2017).",
    ))


def parse_outcome(reply: str) -> dict[str, str]:
    """The fields *reply* states, under the names in :data:`FIELDS` (pure).

    First occurrence wins per field, so a reply that restates one after its prose cannot
    overwrite the answer it opened with.
    """
    stated: dict[str, str] = {}
    for line in reply.splitlines():
        key, separator, value = line.partition(":")
        name = key.strip().lower()
        if separator and name in FIELDS and name not in stated:
            stated[name] = value.strip()
    return stated


def refusals(stated: Mapping[str, str]) -> tuple[str, ...]:
    """Every way *stated* fails the §3.2 contract; empty when it satisfies it (pure)."""
    tier = stated.get("tier", "")
    found = []
    if not stated.get("control"):
        found.append("no control is named")
    if tier not in TIERS:
        found.append(f"tier '{tier}' is not one of {', '.join(TIERS)}")
    if not stated.get("defect-class"):
        found.append("no class of defects is named")
    if tier == DOCUMENTATION_TIER and not stated.get("downgrade-reason"):
        found.append(
            "a documentation tier is a downgrade and must record why no stronger "
            "control was available"
        )
    if stated.get("chain") and not stated.get("branch-not-taken"):
        found.append("a causal chain must carry the branch not taken beside it")
    return tuple(found)


def settle(repo_root: Path, root_issue: str, reply: str) -> str:
    """Record the retrospector's outcome on *root_issue*; return what was recorded.

    A reply that misses the contract is recorded as a refusal naming the missing field
    rather than discarded: the dispatch was paid for either way.

    Nothing here writes to the catalog. §3.2's outcome lands as a diff against catalog
    YAML under D25, admitted by a human at any grant level, so a marker is the whole of
    what this module does with it.
    """
    stated = parse_outcome(reply)
    missing = refusals(stated)
    detail = (
        f"refused: {'; '.join(missing)}"
        if missing
        else f"{stated['tier']} tier: {stated['control']} covers {stated['defect-class']}"
    )
    tracker.try_add_comment(repo_root, root_issue, f"{MARKER} outcome {detail}\n{reply.strip()}")
    return detail

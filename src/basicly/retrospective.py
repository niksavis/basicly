from __future__ import annotations

import itertools
import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

from . import policy, tracker

MARKER = "[harness-retro]"

PHASE = "retrospective"

_SIGMA_MULTIPLE = 3

MIN_OBSERVATIONS = 7

MIN_SPECIAL_COUNT = 2

RUN_LENGTH = 7
TREND_LENGTH = 6

BEYOND_LIMITS = "beyond-limits"
RUN = "run"
TREND = "trend"

CONTROL_TIER = "control"
WARNING_TIER = "warning"
DOCUMENTATION_TIER = "documentation"
TIERS = (CONTROL_TIER, WARNING_TIER, DOCUMENTATION_TIER)


@dataclass(frozen=True)
class Point:
    issue_id: str
    failures: int


@dataclass(frozen=True)
class Chart:
    centre: float
    sigma: float
    upper: float
    observations: int


@dataclass(frozen=True)
class Signal:
    fires: bool
    chart: Chart
    rule: str = ""
    point: str = ""
    detail: str = ""


def read_ledger(repo_root: Path, root_issue: str) -> tuple[Point, ...]:

    return tuple(
        Point(issue_id, policy.rework_recorded(repo_root, issue_id))
        for issue_id in policy.session_issue_ids(repo_root, root_issue)
    )


def chart(points: Sequence[Point]) -> Chart:

    counts = [point.failures for point in points]
    centre = sum(counts) / len(counts) if counts else 0.0
    sigma = math.sqrt(centre)
    return Chart(centre, sigma, centre + _SIGMA_MULTIPLE * sigma, len(counts))


def evaluate(points: Sequence[Point]) -> Signal:

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

    for end in range(len(counts), length - 1, -1):
        if accepts(counts[end - length : end]):
            return end - 1
    return None


def _fired_marker(signal: Signal) -> str:
    return f"{MARKER} fired rule={signal.rule} point={signal.point}"


def claim(repo_root: Path, root_issue: str, signal: Signal) -> bool:

    marker = _fired_marker(signal)
    if any(
        _first_line(str(comment.get("text", ""))) == marker
        for comment in tracker.try_read_comments(repo_root, root_issue)
    ):
        return False
    return tracker.try_add_comment(repo_root, root_issue, f"{marker}\n{signal.detail}")


def _first_line(text: str) -> str:
    stripped = text.strip()
    return stripped.splitlines()[0] if stripped else ""


FIELDS = ("control", "tier", "defect-class", "downgrade-reason", "chain", "branch-not-taken")


def prompt(root_issue: str, signal: Signal) -> str:
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

    stated: dict[str, str] = {}
    for line in reply.splitlines():
        key, separator, value = line.partition(":")
        name = key.strip().lower()
        if separator and name in FIELDS and name not in stated:
            stated[name] = value.strip()
    return stated


def refusals(stated: Mapping[str, str]) -> tuple[str, ...]:
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

    stated = parse_outcome(reply)
    missing = refusals(stated)
    detail = (
        f"refused: {'; '.join(missing)}"
        if missing
        else f"{stated['tier']} tier: {stated['control']} covers {stated['defect-class']}"
    )
    tracker.try_add_comment(repo_root, root_issue, f"{MARKER} outcome {detail}\n{reply.strip()}")
    return detail

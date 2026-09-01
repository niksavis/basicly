"""Fail when a lane's recorded spend forecast misses its actual past the band, unless frozen.

`decompose.spend_accuracy` holds every bead's forecast against what its dispatches really
spent, over the committed markers and the local run records. A pair outside
`SPEND_RATIO_BAND` is a finding about the estimator, and the recorded forecast never
changes, so the finding is permanent history. With nowhere to bank it, one pair turned
main red for every landing on 2026-08-06, 2026-08-08 and 2026-08-28 (basicly-helmej).

`[tool.spend_accuracy.frozen]` is that place, and it tracks rather than falls, as
`release-notes` does: a frozen record must still violate, so a pair the estimator later
brings in band graduates and its entry has to go in the same diff.

Run::

    uv run python .scripts/check_spend_accuracy.py
"""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import (  # noqa: E402 - the path above comes first
    MAY_ONLY_TRACK,
    Finding,
    Ratchet,
    RatchetError,
    compose_ratchet,
    count_delta_remedy,
    fragment,
    frozen_table,
    report,
)

from basicly import decompose  # noqa: E402 - the path above comes first
from basicly.config import load_sizing_config  # noqa: E402 - the path above comes first

_GATE = "spend_accuracy"
LABEL = "spend-accuracy"
COUNT_KEY = "frozen_count"
FROZEN_TABLE = frozen_table(_GATE)
FROZEN_FRAGMENT = fragment(f"{_GATE}.frozen")


def load_ratchet(repo: Path) -> Ratchet[int]:
    """The frozen records, one each: a record is outside the band or it is not."""
    return compose_ratchet(
        repo, _GATE, count_key=COUNT_KEY, entry_type=int, may_only=MAY_ONLY_TRACK
    )


def outside_band(accuracy: decompose.SpendAccuracy) -> dict[str, str]:
    """Each violating record to the sentence :func:`decompose.spend_accuracy` states."""
    found: dict[str, str] = {}
    for line in accuracy.violations:
        bead = line.split(" ", 1)[0]
        found[bead] = line
    return found


def collect(found: Mapping[str, str], ratchet: Ratchet[int]) -> list[Finding]:
    """Every disagreement between the measured pairs and the frozen table, both ways.

    The subjects are the union, so a frozen entry is visited whether or not the tree still
    produces its violation: an entry satisfied by never being looked at is how a table
    stops shrinking.
    """
    findings: list[Finding] = []
    for subject in sorted(set(found) | set(ratchet.frozen)):
        if subject in found and subject not in ratchet.frozen:
            findings.append(
                Finding(
                    subject=subject,
                    detail=found[subject],
                    remedy=(
                        "fix the estimator, never the band; or bank the record as history: "
                        f'`"{subject}" = 1` in {FROZEN_TABLE} with a comment naming the '
                        f"defect, and {count_delta_remedy(_GATE, 1)}"
                    ),
                )
            )
        elif subject not in found:
            findings.append(
                Finding(
                    subject=subject,
                    detail=(
                        f"{FROZEN_TABLE} records it outside the band, but its pair is in band "
                        "or gone"
                    ),
                    remedy=(
                        f'record `"{subject}" = -1` in {FROZEN_FRAGMENT} — an entry describing '
                        "nothing licenses the next miss for free"
                    ),
                )
            )
    if len(ratchet.frozen) != ratchet.count:
        findings.append(
            Finding(
                subject="pyproject.toml",
                detail=f"{len(ratchet.frozen)} frozen record(s) but {COUNT_KEY} is {ratchet.count}",
                remedy=count_delta_remedy(_GATE, len(ratchet.frozen) - ratchet.count),
            )
        )
    return findings


def summary(accuracy: decompose.SpendAccuracy, ratchet: Ratchet[int]) -> str:
    """The pass line: what was measured, what missed, and how much of that is banked."""
    return (
        f"{LABEL}: {len(accuracy.pairs)} pair(s) measured, {len(accuracy.violations)} outside "
        f"the {decompose.SPEND_RATIO_BAND:.0f}x band, {len(ratchet.frozen)} frozen; "
        f"{len(accuracy.unscoped)} unscoped, {len(accuracy.incomparable)} incomparable, "
        f"{accuracy.aborted} aborted, {len(accuracy.unfinished)} still open"
    )


def main(argv: list[str] | None = None) -> int:
    """Entry point: refuse a spend forecast that missed by an order of magnitude, unbanked."""
    del argv
    try:
        ratchet = load_ratchet(REPO_ROOT)
    except RatchetError as exc:
        print(f"{LABEL}: {exc}", file=sys.stderr)
        return 1
    accuracy = decompose.spend_accuracy(REPO_ROOT, load_sizing_config(REPO_ROOT))
    findings = collect(outside_band(accuracy), ratchet)
    if findings:
        report(LABEL, findings)
        return 1
    print(summary(accuracy, ratchet))
    return 0


if __name__ == "__main__":
    sys.exit(main())

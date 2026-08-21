"""Fail when a waiver has outlived the record that was to retire it, and state the total.

Each size ratchet ratcheted its own waiver count and nothing counted them together or read
one again after it was granted (basicly-twfj): two landed in one session from two lanes that
were not about size, both argued honestly, and what a reviewer saw was a parenthetical
number inside one gate's pass line.

**The expiry is what the cohesion/cost distinction is for.** A cohesion waiver is permanent
and owes nothing. A cost waiver stood in for work and named the record doing it, so the
moment that record closes the exemption is a licence nobody voted for — the fail-open shape
this repo keeps paying for. A retiring record the tracker does not hold fails too: an id
that resolves to nothing can never close, so its expiry would never fire.

Separate from `module-size` and `comment-density` rather than folded into either. Each of
those polices the shape of its own waivers, because a gate that grants one is where the
message belongs; neither can state a cross-gate total, and neither should learn to read the
tracker when it already walks 400 files on every commit.

Run::

    uv run python .scripts/check_waivers.py
"""

from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

import check_comment_density  # noqa: E402 - the paths above come first
import check_module_size  # noqa: E402 - the paths above come first
from ratchet import (  # noqa: E402 - the paths above come first
    Finding,
    RatchetError,
    report,
    tracked_sources,
)
from waivers import (  # noqa: E402 - the paths above come first
    Waiver,
    expired,
    read_waiver,
    unknown_retirer,
)

from basicly import config, tracker  # noqa: E402 - the paths above come first

# The gates that grant a waiver, each with the marker it grants it under, imported rather
# than respelled so a marker renamed in its own gate cannot fall out of the census. A gate
# with no waiver of its own — `noqa-debt` counts unreasoned suppressions, not waivers — is
# absent because it has nothing to contribute, not because it was forgotten.
GRANTING_GATES = (
    (check_module_size.LABEL, check_module_size.WAIVER_MARKER),
    (check_comment_density.LABEL, check_comment_density.WAIVER_MARKER),
)

CLOSED = "closed"

_LABEL = "waivers"


def granted(repo: Path) -> list[Waiver]:
    """Every waiver in the tree, across every gate that grants one, ordered by subject.

    One walk for both markers: a module may carry a waiver from each gate, and both are
    separate grants against separate counts.

    Raises:
        RatchetError: git refused to list the tree.
    """
    found = [
        waiver
        for name, text in tracked_sources(repo)
        for _, marker in GRANTING_GATES
        if (waiver := read_waiver(name, text, marker)) is not None
    ]
    return sorted(found, key=lambda waiver: (waiver.subject, waiver.kind))


def record_statuses(repo: Path) -> dict[str, str]:
    """Every record the tracker holds, by id, with the status a cost waiver expires on.

    Reached through the committed ledger the way `check_release_notes.py` does, so this
    runs in a fresh clone with no tracker binary; the `config` call installs the mode reader
    the owned store refuses to answer without.
    """
    config.load_tracker_mode(repo)
    return {
        str(record.get("id")): str(record.get("status")) for record in tracker.all_records(repo)
    }


def collect(waivers: Iterable[Waiver], statuses: Mapping[str, str]) -> list[Finding]:
    """Every cost waiver that has outlived, or can never be reached by, its retiring record.

    An unclassified waiver is not reported here. `module-size` and `comment-density` each
    fail on their own, and repeating it would print one defect twice under two labels.
    """
    findings = []
    for waiver in waivers:
        if not waiver.debt:
            continue
        status = statuses.get(str(waiver.retires))
        if status is None:
            findings.append(unknown_retirer(waiver))
        elif status == CLOSED:
            findings.append(expired(waiver))
    return sorted(findings, key=lambda finding: (finding.subject, finding.detail))


def census(waivers: Iterable[Waiver]) -> str:
    """The one line a reader gets instead of a number inside one gate's pass line.

    Unclassified waivers are counted apart rather than folded into the cohesion half: the
    granting gate is what fails on them, and a total that silently read them as permanent
    would be the same blind number this gate replaced.
    """
    waivers = list(waivers)
    debt = [waiver for waiver in waivers if waiver.debt]
    unclassified = [waiver for waiver in waivers if not waiver.kind]
    cohesion = len(waivers) - len(debt) - len(unclassified)
    gates = ", ".join(label for label, _ in GRANTING_GATES)
    owing = f" ({', '.join(waiver.subject for waiver in debt)})" if debt else ""
    unstated = f", {len(unclassified)} unclassified" if unclassified else ""
    return (
        f"{_LABEL}: {len(waivers)} granted across {gates} — "
        f"{cohesion} bought on cohesion, {len(debt)} debt{owing}{unstated}"
    )


def main() -> int:
    """Entry point: report every expired waiver, then state the total and its split."""
    try:
        waivers = granted(REPO_ROOT)
        statuses = record_statuses(REPO_ROOT)
    except RatchetError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    findings = collect(waivers, statuses)
    if findings:
        report(_LABEL, findings)
        return 1
    print(census(waivers))
    return 0


if __name__ == "__main__":
    sys.exit(main())

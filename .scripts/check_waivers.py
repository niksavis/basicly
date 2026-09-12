from __future__ import annotations

import sys
from collections.abc import Iterable, Mapping
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT = SCRIPT_DIR.parent
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(SCRIPT_DIR))

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

GRANTING_GATES = ((check_module_size.LABEL, check_module_size.WAIVER_MARKER),)

CLOSED = "closed"

_LABEL = "waivers"


def granted(repo: Path) -> list[Waiver]:

    found = [
        waiver
        for name, text in tracked_sources(repo)
        for _, marker in GRANTING_GATES
        if (waiver := read_waiver(name, text, marker)) is not None
    ]
    return sorted(found, key=lambda waiver: (waiver.subject, waiver.kind))


def record_statuses(repo: Path) -> dict[str, str]:

    config.load_tracker_mode(repo)
    return {
        str(record.get("id")): str(record.get("status")) for record in tracker.all_records(repo)
    }


def collect(waivers: Iterable[Waiver], statuses: Mapping[str, str]) -> list[Finding]:

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

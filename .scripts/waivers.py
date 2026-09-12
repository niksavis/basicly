from __future__ import annotations

import re
import sys
from collections.abc import Collection
from dataclasses import dataclass
from pathlib import Path

SCRIPT_DIR = Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPT_DIR))

from ratchet import Finding, count_delta_remedy  # noqa: E402 - the path above comes first


def waiver_reason(text: str, marker: str) -> str | None:

    match = re.search(
        rf"^#[ \t]*{re.escape(marker)}:[ \t]*(\S.*?)[ \t]*$", text, flags=re.MULTILINE
    )
    return match.group(1) if match else None


COHESION = "cohesion"
COST = "cost"

UNCLASSIFIED = ""

_KIND = re.compile(rf"^(?:({COHESION})|{COST}\(([^()\s]+)\)):[ \t]*(\S.*)$")


@dataclass(frozen=True)
class Waiver:
    subject: str
    kind: str
    retires: str | None
    reason: str

    @property
    def debt(self) -> bool:
        return self.kind == COST


def read_waiver(subject: str, text: str, marker: str) -> Waiver | None:

    reason = waiver_reason(text, marker)
    if reason is None:
        return None
    match = _KIND.match(reason)
    if match is None:
        return Waiver(subject, UNCLASSIFIED, None, reason)
    cohesion, retires, rest = match.groups()
    return Waiver(subject, COHESION if cohesion else COST, retires, rest)


def unclassified_waiver(marker: str, waiver: Waiver) -> Finding:
    return Finding(
        subject=waiver.subject,
        detail=(
            f"`{marker}:` states no kind, so nothing says whether this is permanent or owed back"
        ),
        remedy=(
            f"write `# {marker}: {COHESION}: <reason>` when the module's size or prose is "
            f"the contract it carries, or `# {marker}: {COST}(<record-id>): <reason>` when "
            "the ratchet refused a change whose real fix is out of scope"
        ),
    )


def expired(waiver: Waiver) -> Finding:
    return Finding(
        subject=waiver.subject,
        detail=(
            f"waived on {COST} against `{waiver.retires}`, which is closed; the work the "
            "waiver stood in for is done and the exemption is still here"
        ),
        remedy=(
            "delete the waiver comment and record the count delta the gate's finding names, "
            f"or name the record that now retires it in `{COST}(<record-id>)`"
        ),
    )


def unknown_retirer(waiver: Waiver) -> Finding:
    return Finding(
        subject=waiver.subject,
        detail=(
            f"waived on {COST} against `{waiver.retires}`, which names no record the tracker "
            "holds, so nothing can ever expire it"
        ),
        remedy=f"name the record that retires this waiver in `{COST}(<record-id>)`",
    )


def waiver_findings(gate: str, waived: Collection[str], recorded: int) -> list[Finding]:
    listed_paths = sorted(waived)
    if len(listed_paths) == recorded:
        return []
    direction = "added" if len(listed_paths) > recorded else "removed"
    listed = ", ".join(listed_paths) or "none"
    return [
        Finding(
            subject="pyproject.toml",
            detail=(
                f"{len(listed_paths)} module(s) carry a waiver but waiver_count is "
                f"{recorded} — a waiver was {direction} without saying so (waived: {listed})"
            ),
            remedy=count_delta_remedy(gate, len(listed_paths) - recorded),
        )
    ]

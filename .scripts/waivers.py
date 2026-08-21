"""The waiver record: what buys an exemption from a size ratchet, and what it owes back.

`ratchet.py` holds the count; this holds what the count is *of*, because a count is the one
thing a reader cannot act on. The two kinds are defined at :data:`COHESION`, and
`check_waivers.py` is the census and the expiry over them.

**The kind has to live on the marker line, not in a table.** A table naming each waived path
would collide between lanes exactly as the shared `frozen` table did before basicly-ef7t,
and nothing outside the file can say *which* module a granted count belongs to — so an
expiry check reading a table would have no subject to look for.

Stdlib only, because the gates importing this run on every commit.
"""

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
    """The reason *text* waives *marker*'s cap with, or ``None`` if it does not waive it.

    *marker* is spelled without its colon. The pattern is built rather than held as a
    constant so that this module names no gate's marker and therefore cannot waive itself.
    """
    match = re.search(
        rf"^#[ \t]*{re.escape(marker)}:[ \t]*(\S.*?)[ \t]*$", text, flags=re.MULTILINE
    )
    return match.group(1) if match else None


# The two things a waiver can be bought with, and the difference is whether a follow-up is
# owed. **Cohesion** is a module whose prose or size is the contract it carries: correct,
# permanent, nothing to retire. **Cost** is the ratchet having refused a change whose real
# fix was out of scope, which is debt and must name the record that clears it. Before
# basicly-twfj a reader saw only a count, and the two were indistinguishable inside it.
COHESION = "cohesion"
COST = "cost"

# What a waiver whose reason states neither kind is, kept as a value rather than a `None` so
# a census can carry it and report it instead of dropping it out of the total.
UNCLASSIFIED = ""

# `<kind>: <reason>`, where a cost waiver names its retiring record in parentheses. Anchored
# at the head of the reason `waiver_reason` already read, so the kind is on the marker line
# and not somewhere in the paragraph under it.
_KIND = re.compile(rf"^(?:({COHESION})|{COST}\(([^()\s]+)\)):[ \t]*(\S.*)$")


@dataclass(frozen=True)
class Waiver:
    """One granted waiver: what it exempts, what bought it, and what retires it.

    *retires* is the record a cost waiver expires with, and is ``None`` for the other two
    kinds — a cohesion waiver has nothing to expire, and an unclassified one has not said.
    """

    subject: str
    kind: str
    retires: str | None
    reason: str

    @property
    def debt(self) -> bool:
        """Whether this waiver is owed back rather than permanent."""
        return self.kind == COST


def read_waiver(subject: str, text: str, marker: str) -> Waiver | None:
    """*text*'s waiver against *marker*, or ``None`` if it carries none.

    An unstated or malformed kind is returned as :data:`UNCLASSIFIED` rather than refused
    here: the granting gate reports it as a finding, so the message names the marker the
    reader has to fix and not this module's grammar.
    """
    reason = waiver_reason(text, marker)
    if reason is None:
        return None
    match = _KIND.match(reason)
    if match is None:
        return Waiver(subject, UNCLASSIFIED, None, reason)
    cohesion, retires, rest = match.groups()
    return Waiver(subject, COHESION if cohesion else COST, retires, rest)


def unclassified_waiver(marker: str, waiver: Waiver) -> Finding:
    """A waiver that states no kind, so the record cannot say whether it is debt."""
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
    """A cost waiver whose retiring record has closed, so nothing is coming to remove it."""
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
    """A cost waiver naming an id the tracker does not hold, so its expiry can never fire."""
    return Finding(
        subject=waiver.subject,
        detail=(
            f"waived on {COST} against `{waiver.retires}`, which names no record the tracker "
            "holds, so nothing can ever expire it"
        ),
        remedy=f"name the record that retires this waiver in `{COST}(<record-id>)`",
    )


def waiver_findings(gate: str, waived: Collection[str], recorded: int) -> list[Finding]:
    """The waiver-count ratchet, which moves only in a diff that says it moved."""
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

"""The recorded form of a plan: how a bead body carries it, and how it reads back.

One responsibility, and it is the round trip. :func:`render_plan_section` is the only
writer of the ``## Plan`` block and :func:`parse_plan_section` the only reader, so the
predicate that gates a dispatch cannot end up reading a shape the decomposer stopped
writing. The section readers below are here for the same reason: they are what "a bead
declared this" means in this repo, and both the decomposer and the gate ask through
them rather than each matching the markup itself.

Split out of ``plan_gate`` when that module crossed the module-size cap. The boundary
is *recorded form* against *judgement*: nothing here decides whether a plan is
adequate, and :mod:`basicly.plan_gate` does nothing but. :class:`RecordedPlan`
satisfies ``plan_gate.PlannedFields`` structurally, which is why this module needs no
import from the one that judges it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

ACCEPTANCE_HEADING = "## Acceptance Criteria"
SCOPE_HEADING = "## Scope"
PLAN_HEADING = "## Plan"

# What a recorded `## Plan` line looks like, and the literal that means "declared, and
# there is nothing in it" — distinguishable from an absent line, which is the whole
# point of requiring the declaration.
_PLAN_ENTRY = re.compile(r"^([a-z ]+): (.+)$")
_BULLET_LINE = re.compile(r"^- (.+)$")
_BACKTICKED = re.compile(r"^`([^`]+)`$")
NOTHING_DECLARED = "none"

_PLAN_LINE_KEYS = {"integrity": "integrity", "budget": "budget_tokens", "depends on": "depends_on"}


# --- Section reading --------------------------------------------------------


def section_entries(description: str, heading: str) -> tuple[str, ...]:
    """The ``- `` bullet entries recorded under *heading*, marker stripped.

    Stops at the next ``## `` heading, so a section's entries are its own. A heading
    that is absent and one that is empty both yield an empty tuple: they are the same
    answer to "what did this bead declare here", and the caller that needs to tell
    them apart reads the headings itself.
    """
    entries: list[str] = []
    inside = False
    for line in description.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            inside = stripped == heading
            continue
        if inside:
            match = _BULLET_LINE.match(stripped)
            if match:
                entries.append(match.group(1).strip())
    return tuple(entries)


def has_heading(description: str, heading: str) -> bool:
    """Whether *description* carries *heading*, with or without entries under it.

    The distinction :func:`section_entries` deliberately collapses: an absent heading
    and an empty one are the same answer to "what did this declare", and different
    answers to "was this section ever written".
    """
    return any(line.strip() == heading for line in description.splitlines())


def backticked_entries(description: str, heading: str) -> tuple[str, ...]:
    """The entries under *heading* that are one backticked value, unquoted.

    The strict form is what makes a declared scope machine-readable: prose under the
    heading is not a glob, and reading it as one would let a bead look sized when
    nothing can size it.
    """
    matches = (_BACKTICKED.match(entry) for entry in section_entries(description, heading))
    return tuple(match.group(1) for match in matches if match)


# --- The round trip ---------------------------------------------------------


@dataclass(frozen=True)
class RecordedPlan:
    """The plan fields read back off a bead body, each absent as ``None``."""

    acceptance: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    depends_on: tuple[str, ...] | None = None
    budget_tokens: int | None = None
    integrity: str | None = None


def render_plan_section(depends_on: tuple[str, ...], budget_tokens: int, integrity: str) -> str:
    """The ``## Plan`` body :func:`parse_plan_section` reads back.

    One writer and one reader for the recorded form, so the build entry predicate
    cannot be reading a shape the decomposer stopped writing.
    """
    declared = ", ".join(f"`{dep}`" for dep in depends_on) if depends_on else NOTHING_DECLARED
    return "\n".join((
        f"- integrity: `{integrity}`",
        f"- budget: `{budget_tokens}`",
        f"- depends on: {declared}",
    ))


def parse_plan_section(description: str) -> RecordedPlan:
    """The five plan fields recorded on a bead body, with absences kept as absences.

    Structural, exactly like :func:`decompose.parse_scope_section`: a heading with
    entries under it counts, and what those entries *say* is not judged here. That
    leaves one lenient edge — ``policy.scaffold_body`` writes its unfilled acceptance
    hint as a bullet, so a scaffold nobody filled in reads as having an acceptance
    criterion. It is deliberately not special-cased. Refusing a ``TODO`` placeholder
    would be a second, stricter answer to the question the Definition of Ready already
    owns, and the case cannot arrive from the decomposer anyway: the plan gate requires
    a non-empty acceptance list before a child is ever created, and such a bead is
    refused there regardless by the four fields a scaffold does not carry.
    """
    values: dict[str, str] = {}
    for entry in section_entries(description, PLAN_HEADING):
        match = _PLAN_ENTRY.match(entry)
        if match and match.group(1) in _PLAN_LINE_KEYS:
            values[_PLAN_LINE_KEYS[match.group(1)]] = match.group(2).strip()

    return RecordedPlan(
        acceptance=section_entries(description, ACCEPTANCE_HEADING),
        scope=backticked_entries(description, SCOPE_HEADING),
        depends_on=_parse_recorded_list(values.get("depends_on")),
        budget_tokens=_parse_recorded_budget(values.get("budget_tokens")),
        integrity=_parse_recorded_scalar(values.get("integrity")),
    )


def _parse_recorded_scalar(value: str | None) -> str | None:
    if value is None:
        return None
    match = _BACKTICKED.match(value)
    return match.group(1) if match else None


def _parse_recorded_budget(value: str | None) -> int | None:
    text = _parse_recorded_scalar(value)
    if text is None or not text.isdigit():
        return None
    return int(text)


def _parse_recorded_list(value: str | None) -> tuple[str, ...] | None:
    """A recorded dependency list: ``None`` when absent, ``()`` when declared empty."""
    if value is None:
        return None
    if value.strip() == NOTHING_DECLARED:
        return ()
    matches = (_BACKTICKED.match(item.strip()) for item in value.split(","))
    return tuple(match.group(1) for match in matches if match)

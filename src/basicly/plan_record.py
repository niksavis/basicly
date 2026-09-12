from __future__ import annotations

import re
from dataclasses import dataclass

ACCEPTANCE_HEADING = "## Acceptance Criteria"
SCOPE_HEADING = "## Scope"
PLAN_HEADING = "## Plan"

_PLAN_ENTRY = re.compile(r"^([a-z ]+): (.+)$")
_BULLET_LINE = re.compile(r"^- (.+)$")
_BACKTICKED = re.compile(r"^`([^`]+)`$")
NOTHING_DECLARED = "none"

_PLAN_LINE_KEYS = {
    "integrity": "integrity",
    "budget": "budget_tokens",
    "depends on": "depends_on",
    "demonstration": "demonstration",
}


def section_entries(description: str, heading: str) -> tuple[str, ...]:

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

    return any(line.strip() == heading for line in description.splitlines())


def backticked_entries(description: str, heading: str) -> tuple[str, ...]:

    matches = (_BACKTICKED.match(entry) for entry in section_entries(description, heading))
    return tuple(match.group(1) for match in matches if match)


@dataclass(frozen=True)
class RecordedPlan:
    acceptance: tuple[str, ...] = ()
    scope: tuple[str, ...] = ()
    depends_on: tuple[str, ...] | None = None
    budget_tokens: int | None = None
    integrity: str | None = None
    demonstration: str | None = None


def render_plan_section(
    depends_on: tuple[str, ...], budget_tokens: int, integrity: str, demonstration: str
) -> str:

    declared = ", ".join(f"`{dep}`" for dep in depends_on) if depends_on else NOTHING_DECLARED
    return "\n".join((
        f"- integrity: `{integrity}`",
        f"- budget: `{budget_tokens}`",
        f"- depends on: {declared}",
        f"- demonstration: {demonstration}",
    ))


def parse_plan_section(description: str) -> RecordedPlan:

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
        demonstration=values.get("demonstration"),
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
    if value is None:
        return None
    if value.strip() == NOTHING_DECLARED:
        return ()
    matches = (_BACKTICKED.match(item.strip()) for item in value.split(","))
    return tuple(match.group(1) for match in matches if match)

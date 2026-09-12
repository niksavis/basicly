from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from .config import DEFAULT_TYPE_SECTIONS, load_type_sections
from .plan_record import ACCEPTANCE_HEADING, has_heading, section_entries

TRIGGER_HEADING = "## Trigger"

JOB_STORY_EXAMPLE = "When <situation>, I want to <motivation>, so I can <outcome>."
USER_STORY_EXAMPLE = "As a <persona>, I want <goal>, so that <benefit>."

_SPAN = 400
_JOB_STORY = re.compile(
    rf"\bwhen\b.{{0,{_SPAN}}}?\bi want\b.{{0,{_SPAN}}}?\bso (?:i|we) can\b",
    re.IGNORECASE | re.DOTALL,
)
_USER_STORY = re.compile(
    rf"\bas an?\b.{{0,200}}?\bi want\b.{{0,{_SPAN}}}?\bso that\b",
    re.IGNORECASE | re.DOTALL,
)

_PLACEHOLDER = re.compile(r"<[^>]+>|\bTODO\b")


def trigger_voice(description: str) -> str | None:

    for voice, pattern in (("job", _JOB_STORY), ("user", _USER_STORY)):
        for match in pattern.finditer(description):
            if not _PLACEHOLDER.search(match.group(0)):
                return voice
    return None


def trigger_sentence(description: str) -> str:

    for pattern in (_JOB_STORY, _USER_STORY):
        for match in pattern.finditer(description):
            if not _PLACEHOLDER.search(match.group(0)):
                return match.group(0).strip()
    return ""


def trigger_remedy() -> str:
    return (
        f"state the trigger in either voice - a situation, {JOB_STORY_EXAMPLE!r}, or a "
        f"persona, {USER_STORY_EXAMPLE!r}. A persona is never required: where a situation "
        "triggers the work and no person wants it, inventing a persona is the defect."
    )


def required_conditions(work_type: str, repo_root: Path | None = None) -> tuple[str, ...]:

    declared = DEFAULT_TYPE_SECTIONS if repo_root is None else load_type_sections(repo_root)
    return (TRIGGER_HEADING, *declared.get(work_type, ()), ACCEPTANCE_HEADING)


def owed(states: Iterable[Any], repo_root: Path) -> dict[str, tuple[str, ...]]:

    declared = load_type_sections(repo_root)
    return {
        state.record: missing_sections(
            state.fields,
            (
                TRIGGER_HEADING,
                *declared.get(str(state.fields.get("issue_type") or ""), ()),
                ACCEPTANCE_HEADING,
            ),
        )
        for state in states
    }


def missing_sections(record: Mapping[str, object], required: Sequence[str]) -> tuple[str, ...]:

    described = record.get("description")
    body = described if isinstance(described, str) else ""
    checks = {
        TRIGGER_HEADING: lambda: trigger_voice(body) is not None,
        ACCEPTANCE_HEADING: lambda: _states_acceptance(record, body),
    }
    return tuple(
        section
        for section in required
        if not checks.get(section, lambda s=section: has_heading(body, s))()
    )


def _states_acceptance(record: Mapping[str, object], body: str) -> bool:

    field = record.get("acceptance_criteria")
    if isinstance(field, str) and _states_something(field):
        return True
    return any(_states_something(entry) for entry in section_entries(body, ACCEPTANCE_HEADING))


def _states_something(text: str) -> bool:
    return bool(text.strip()) and not _PLACEHOLDER.search(text)

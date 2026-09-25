from __future__ import annotations

import re
from collections.abc import Iterable, Mapping, Sequence
from pathlib import Path
from typing import Any

from . import tracker
from .config import DEFAULT_TYPE_SECTIONS, load_type_sections
from .plan_record import ACCEPTANCE_HEADING, has_heading

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
_CODE_SPAN = re.compile(r"`[^`\n]*`")


def unfilled(text: str) -> bool:
    return bool(_PLACEHOLDER.search(_CODE_SPAN.sub("", text)))


def trigger_sentence(description: str) -> str:

    for pattern in (_JOB_STORY, _USER_STORY):
        for match in pattern.finditer(description):
            if not unfilled(match.group(0)):
                return match.group(0).strip()
    return ""


def trigger_remedy() -> str:
    return (
        f"state the trigger in either voice - a situation, {JOB_STORY_EXAMPLE!r}, or a "
        f"persona, {USER_STORY_EXAMPLE!r}. A persona is never required: where a situation "
        "triggers the work and no person wants it, inventing a persona is the defect."
    )


def _required(work_type: str, declared: Mapping[str, Sequence[str]], template: Any):

    base = (TRIGGER_HEADING, *declared.get(work_type, ()), ACCEPTANCE_HEADING)
    if template is None:
        return base
    head = base if template.extends else ()
    return tuple(dict.fromkeys((*head, *template.sections, *template.for_type(work_type))))


def required_conditions(work_type: str, repo_root: Path | None = None) -> tuple[str, ...]:

    if repo_root is None:
        return _required(work_type, DEFAULT_TYPE_SECTIONS, None)
    return _required(work_type, load_type_sections(repo_root), tracker.ledger_template(repo_root))


def missing_for(
    record: Mapping[str, object],
    work_type: str,
    repo_root: Path,
    declared: Mapping[str, Sequence[str]] | None = None,
    template: Any = None,
) -> tuple[str, ...]:

    declared = load_type_sections(repo_root) if declared is None else declared
    typed = {**record, "issue_type": work_type} if work_type else dict(record)
    kit_order, shared = tracker.readiness(repo_root, typed, template)
    own = declared.get(work_type, ()) if template is None or template.extends else ()
    described = record.get("description")
    body = described if isinstance(described, str) else ""
    own_missing = {heading for heading in own if not _held(record, body, heading)}
    head = kit_order[:1] if kit_order[:1] == (TRIGGER_HEADING,) else ()
    order = dict.fromkeys((*head, *own, *kit_order))
    return tuple(heading for heading in order if heading in shared or heading in own_missing)


def owed(states: Iterable[Any], repo_root: Path) -> dict[str, tuple[str, ...]]:

    declared = load_type_sections(repo_root)
    template = tracker.ledger_template(repo_root)
    return {
        state.record: missing_for(
            state.fields,
            str(state.fields.get("issue_type") or ""),
            repo_root,
            declared,
            template,
        )
        for state in states
    }


def _field_of(heading: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", heading.lstrip("#").strip().lower()).strip("_")


def _held(record: Mapping[str, object], body: str, heading: str) -> bool:

    value = record.get(_field_of(heading))
    return has_heading(body, heading) or (isinstance(value, str) and _states_something(value))


def _states_something(text: str) -> bool:
    return bool(text.strip()) and not unfilled(text)

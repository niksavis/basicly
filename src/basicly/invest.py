from __future__ import annotations

import importlib.util
import re
from collections.abc import Iterable, Mapping, Sequence
from functools import lru_cache
from pathlib import Path
from types import ModuleType
from typing import Any

from . import tracker
from .catalog import bundled_catalog_root
from .config import DEFAULT_TYPE_SECTIONS, load_type_sections
from .plan_record import ACCEPTANCE_HEADING, has_heading

TRIGGER_HEADING = "## Trigger"


@lru_cache(maxsize=1)
def _shaping() -> ModuleType:
    source = bundled_catalog_root() / "kit" / "tracker" / "shaping.py"
    spec = importlib.util.spec_from_file_location("basicly_invest_shaping", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"the bundled tracker shaping rules cannot load from {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def unfilled(text: str) -> bool:
    return bool(_shaping().unfilled(text))


def trigger_sentence(description: str) -> str:
    return str(_shaping().trigger_sentence(description))


def trigger_remedy() -> str:
    return str(_shaping().remedy((TRIGGER_HEADING,)))


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
    readiness = tracker.readiness(repo_root, typed, template)
    return _missing_for(record, work_type, declared, template, readiness)


def _missing_for(
    record: Mapping[str, object],
    work_type: str,
    declared: Mapping[str, Sequence[str]],
    template: Any,
    readiness: tuple[tuple[str, ...], frozenset[str]],
) -> tuple[str, ...]:
    kit_order, shared = readiness
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
    found = tracker.kit(repo_root, "events").read_events(tracker.ledger_dir(repo_root))[0]
    return {
        state.record: _missing_for(
            {**state.fields, "id": state.record},
            str(state.fields.get("issue_type") or ""),
            declared,
            template,
            tracker.readiness(
                repo_root, {**state.fields, "id": state.record}, template, found=found
            ),
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

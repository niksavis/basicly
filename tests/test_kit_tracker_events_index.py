from __future__ import annotations

import dataclasses
import importlib.util
import sys
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).parent.parent
EVENTS_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "events.py"
MODULE_NAME = "tracker_events"
RECORD = "basicly-aa11"
CLOCK = 1_000_000_000.0


def _kit() -> ModuleType:

    cached = sys.modules.get(MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(MODULE_NAME, EVENTS_SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


events = _kit()


def _ledger(tmp_path: Path) -> Path:
    ledger = tmp_path / "ledger"
    drafts = [events.Draft(RECORD, events.KIND_CREATED, {"title": "indexed"})]
    drafts += [
        events.Draft(RECORD, events.KIND_COMMENT, {"text": f"note {n}"})
        for n in range(events.MEMO_MIN_EVENTS + 4)
    ]
    events.append(ledger, drafts, clock=lambda: CLOCK)
    return ledger


def test_a_new_event_on_the_ledger_read_path_is_in_the_next_index(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    before = events.record_index(events.read_events(ledger)[0])[1][RECORD]

    events.append(
        ledger, [events.Draft(RECORD, events.KIND_COMMENT, {"text": "late"})], clock=lambda: CLOCK
    )
    after = events.record_index(events.read_events(ledger)[0])[1][RECORD]

    assert len(after) == len(before) + 1
    assert after[-1].payload == {"text": "late"}


def test_an_event_list_changed_in_place_is_never_answered_from_the_old_index(
    tmp_path: Path,
) -> None:
    found = events.read_events(_ledger(tmp_path))[0]
    first_fields, first = events.record_index(found)
    found[-1] = dataclasses.replace(found[-1], payload={"text": "changed"})

    fields, again = events.record_index(found)

    assert first[RECORD][-1].payload != {"text": "changed"}
    assert again[RECORD][-1].payload == {"text": "changed"}
    assert fields is not first_fields


def _process_evidence() -> ModuleType:

    key = "basicly_tracker_kit_process_evidence"
    cached = sys.modules.get(key)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(
        key, EVENTS_SOURCE.with_name("process_evidence.py")
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[key] = module
    spec.loader.exec_module(module)
    return module


def _scanned_revision(pe: ModuleType, found: list, record: str, context: Any) -> str:

    states = pe.events.fields_by_record(found)
    held = dict(states[record])
    headings = pe._headings(held, context.template, context.legacy)
    names = pe.SEMANTIC_FIELDS | frozenset(pe.shaping.field_of(h) for h in headings)
    semantic = sorted({event.id for event in found if pe._semantic(event, record, names)})
    content = {name: held.get(name) for name in sorted(names)}
    edges = {}
    for event in pe.events.canonical_order(found):
        if event.record == record and event.kind in (
            pe.events.KIND_EDGE,
            pe.events.KIND_EDGE_RETRACTED,
        ):
            target_key, type_key = pe.labels.DIALECT_KEYS[pe.labels.edge_dialect(event.payload)]
            edges[(event.payload.get(target_key), event.payload.get(type_key))] = (
                event.kind == pe.events.KIND_EDGE
            )
    targets = sorted({t for (t, _kind), active in edges.items() if active and isinstance(t, str)})
    dependencies = {}
    for target in targets:
        target_headings = pe._headings(states.get(target, {}), context.template, context.legacy)
        target_names = pe.SEMANTIC_FIELDS | frozenset(
            pe.shaping.field_of(h) for h in target_headings
        )
        dependencies[target] = [
            target_headings,
            sorted({e.id for e in found if pe._semantic(e, target, target_names)}),
        ]
    packed = pe.json.dumps(
        [record, semantic, content, [], dependencies, headings],
        sort_keys=True,
        separators=(",", ":"),
    )
    return pe.hashlib.sha256(packed.encode("utf-8")).hexdigest()


def test_the_indexed_revision_equals_a_full_ledger_scan_for_records_edges_and_targets(
    tmp_path: Path,
) -> None:
    pe = _process_evidence()
    made = pe.events
    ledger = tmp_path / "ledger"
    first, second, third = "basicly-aa11", "basicly-bb22", "basicly-cc33"
    drafts = [made.Draft(r, made.KIND_CREATED, {"title": r}) for r in (first, second, third)]
    drafts += [
        made.Draft(first, made.KIND_FIELD, {"name": "acceptance_criteria", "value": "- it works"}),
        made.Draft(first, made.KIND_EDGE, {"target": second, "edge_type": "blocks"}),
        made.Draft(first, made.KIND_EDGE, {"target": third, "edge_type": "blocks"}),
        made.Draft(first, made.KIND_EDGE_RETRACTED, {"target": third, "edge_type": "blocks"}),
        made.Draft(second, made.KIND_FIELD, {"name": "description", "value": "## Context\nwhy"}),
    ]
    drafts += [
        made.Draft(third, made.KIND_COMMENT, {"text": f"note {n}"})
        for n in range(made.MEMO_MIN_EVENTS)
    ]
    made.append(ledger, drafts, clock=lambda: CLOCK)
    found = made.read_events(ledger)[0]

    compared = [
        (record, legacy)
        for record in (first, second, third)
        for legacy in (False, True)
        if pe._revision(found, record, pe.RevisionContext(None, (), None, legacy))
        == _scanned_revision(pe, found, record, pe.RevisionContext(None, (), None, legacy))
    ]

    assert len(found) >= made.MEMO_MIN_EVENTS
    assert len(compared) == 6

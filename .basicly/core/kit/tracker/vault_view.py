from __future__ import annotations

import dataclasses
import importlib.util
import json
import sys
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load(file_name: str, module_name: str) -> Any:

    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError(f"the tracker kit's {file_name} is missing from beside vault_view.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


git_runner = _load("git_runner.py", "basicly_tracker_kit_git_runner")
events = _load("events.py", "basicly_tracker_kit_events")
ids = _load("ids.py", "basicly_tracker_kit_ids")
templates = _load("templates.py", "basicly_tracker_kit_templates")
vault_clone = _load("vault_clone.py", "basicly_tracker_kit_vault_clone")
vault_fold = _load("vault_fold.py", "basicly_tracker_kit_vault_fold")

LEDGER_KIND = "ledger"
LEDGER_FIELD = "ledger"
VIEW_FORMAT = "1"
VIEW_PREFIX = "view-"
ALL_PROJECTS = "all"
KEY_NAME = "view.key"
HOLDER_ACTOR = "vault"
FRESH_S = 30.0
SHALLOW_PROBE = ["rev-parse", "--is-shallow-repository"]

Runner = Callable[..., str]


class ViewError(ValueError):
    pass


@dataclass(frozen=True)
class Want:
    prefix: str
    all_projects: bool = False
    template_from: Path | None = None


def holder_name(holder: Sequence[str] | None) -> str:

    if not holder:
        return ""
    writer, session = holder[0], holder[1]
    return f"{writer}/{session}" if session else str(writer)


def unshallow_command(clone: Any) -> str:
    return f"git -C {clone.bare} fetch --unshallow origin"


def refuse_shallow(clone: Any, runner: Runner, timeout: float) -> None:

    if runner(SHALLOW_PROBE, clone.bare, timeout=timeout).strip() != "true":
        return
    (clone.folder / vault_clone.COVERS_NAME).unlink(missing_ok=True)
    raise ViewError(
        f"the vault clone at {clone.bare} is shallow, so its history is incomplete and the "
        f"tracker cannot read it; run `{unshallow_command(clone)}`, then run this again"
    )


def _in_scope(record: str, want: Want) -> bool:
    return want.all_projects or record.partition(ids.PREFIX_SEP)[0] == want.prefix


def _holder_event(event: Any) -> bool:
    return event.kind == events.KIND_FIELD and event.payload.get("name") == events.HOLDER_FIELD


def carried(vault_events: Iterable[Mapping[str, object]], want: Want) -> list[Any]:

    found: list[Any] = []
    seen: set[str] = set()
    for line in vault_events:
        body = line.get(LEDGER_FIELD)
        if line.get("kind") != LEDGER_KIND or not isinstance(body, dict):
            continue
        try:
            event = events.from_json(json.dumps(body))
        except events.InvalidEventError:
            continue
        if event.id in seen or not _in_scope(event.record, want) or _holder_event(event):
            continue
        seen.add(event.id)
        if event.kind == events.KIND_CREATED and events.HOLDER_FIELD in event.payload:
            payload = {k: v for k, v in event.payload.items() if k != events.HOLDER_FIELD}
            event = dataclasses.replace(event, payload=payload)
        found.append(event)
    return found


def _holder_draft(record: str, holder: Sequence[str]) -> tuple[str, dict[str, object]]:
    payload: dict[str, object] = {"name": events.HOLDER_FIELD, "value": holder_name(holder)}
    return events.event_id_for(record, events.KIND_FIELD, payload), payload


def renumbered(found: Sequence[Any], holders: Mapping[str, Sequence[str]]) -> list[Any]:

    seqs: dict[str, int] = {}
    totals: dict[str, Any] = {}
    stamps: dict[str, str] = {}

    def placed(event: Any) -> Any:
        seq = max(event.seq, seqs.get(event.record, 0) + 1)
        seqs[event.record] = seq
        total = events.accumulate(
            totals.get(event.record, events.Totals()), event.kind, event.payload
        )
        totals[event.record] = total
        stamps[event.record] = event.ts
        return dataclasses.replace(event, seq=seq, totals=total)

    shown = [placed(event) for event in found]
    for record in list(seqs):
        holder = holders.get(record)
        if holder:
            made, payload = _holder_draft(record, holder)
            shown.append(
                placed(
                    events.Event(
                        made, record, 0, events.KIND_FIELD, HOLDER_ACTOR, stamps[record], payload
                    )
                )
            )
    return shown


def _template(want: Want) -> dict[str, object]:

    try:
        held = templates.read(want.template_from) if want.template_from else {}
    except templates.TemplateError as exc:
        raise ViewError(str(exc)) from None
    return {**held, "prefix": want.prefix}


def view_folder(clone: Any, want: Want) -> Path:
    return clone.folder / f"{VIEW_PREFIX}{ALL_PROJECTS if want.all_projects else want.prefix}"


def build(clone: Any, want: Want, key: str, template: Mapping[str, object]) -> Path:

    folder = view_folder(clone, want)
    vault_clone.private(folder)
    valid = vault_clone.valid_events(clone)
    held = {
        name: state.holder
        for name, state in vault_fold.fold(valid).records.items()
        if state.holder is not None
    }
    shown = renumbered(carried(valid, want), held)
    with events.LedgerLock(folder):
        lines = "".join(events.to_json(event) + "\n" for event in shown)
        vault_clone.durable(folder / events.INITIAL_LOG_NAME, lines)
        text = json.dumps(template, indent=2, sort_keys=True) + "\n"
        vault_clone.durable(folder / templates.TEMPLATE_FILE, text)
        vault_clone.durable(folder / KEY_NAME, key)
    return folder


def _stale(clone: Any, clock: Callable[[], float]) -> bool:

    stamp = clone.folder / vault_clone.FETCHED_NAME
    text = stamp.read_text(encoding="utf-8").strip() if stamp.is_file() else ""
    try:
        return clock() - float(text) >= FRESH_S
    except ValueError:
        return True


def _key(clone: Any, template: Mapping[str, object]) -> str:
    covers = clone.folder / vault_clone.COVERS_NAME
    head = covers.read_text(encoding="utf-8").strip() if covers.is_file() else ""
    shape = json.dumps(template, sort_keys=True)
    return f"{VIEW_FORMAT} {head} {shape}\n" if head else ""


def present(
    clone: Any, want: Want, *, clock: Callable[[], float], runner: Runner = git_runner.run
) -> Path:

    timeout = git_runner.DEFAULT_TIMEOUT
    try:
        if _stale(clone, clock):
            vault_clone.fetch(clone, clock=clock, runner=runner, timeout=timeout)
        template = _template(want)
        key = _key(clone, template)
        marker = view_folder(clone, want) / KEY_NAME
        if key and marker.is_file() and marker.read_text(encoding="utf-8") == key:
            return marker.parent
        refuse_shallow(clone, runner, timeout)
        if not key:
            vault_clone.settle(clone, runner=runner, timeout=timeout)
            key = _key(clone, template)
        return build(clone, want, key, template)
    except (vault_clone.CloneError, git_runner.GitRunnerError, events.LedgerError) as exc:
        raise ViewError(f"the tracker could not read the vault {clone.url}: {exc}") from None


def show(directory: Path, clone: Any, records: Sequence[str]) -> list[dict[str, object]]:

    differential = _load("differential.py", "basicly_tracker_kit_differential")
    views = differential.views_from_events(differential.read_ledger(directory))
    folded = vault_fold.fold(vault_clone.valid_events(clone)).records
    shown: list[dict[str, object]] = []
    for record in records:
        view = views.get(record)
        if view is None:
            shown.append({"id": record, "found": False})
            continue
        state = folded.get(record)
        holder = state.holder if state is not None else None
        shown.append({
            "id": record,
            "status": view.status,
            "holder": {"writer": holder[0], "session": holder[1]} if holder else None,
            "links": [{"target": edge.target, "type": edge.type} for edge in view.dependencies],
        })
    return shown

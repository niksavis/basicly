from __future__ import annotations

import dataclasses
import hashlib
import importlib.util
import json
import sys
import tempfile
from collections.abc import Callable, Collection, Mapping, Sequence
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
        raise ImportError(f"the tracker kit's {file_name} is missing from beside vault_migrate.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


git_runner = _load("git_runner.py", "basicly_tracker_kit_git_runner")
events = _load("events.py", "basicly_tracker_kit_events")
redact = _load("redact.py", "basicly_tracker_kit_redact")
snapshot = _load("snapshot.py", "basicly_tracker_kit_snapshot")
templates = _load("templates.py", "basicly_tracker_kit_templates")
migrate = _load("migrate.py", "basicly_tracker_kit_migrate")
beans = _load("beans.py", "basicly_tracker_kit_beans")
vault_clone = _load("vault_clone.py", "basicly_tracker_kit_vault_clone")
vault_view = _load("vault_view.py", "basicly_tracker_kit_vault_view")
vault_share = _load("vault_share.py", "basicly_tracker_kit_vault_share")
vault_shapes = vault_clone.vault_shapes

COMMIT_BYTES = vault_share.SEGMENT_BYTES
SESSION = "migrate"
EVERY_PROJECT = vault_view.Want("", all_projects=True)
_UNPUSHED = "nothing was pushed"
_UNWRITTEN = "nothing was written"

Redactor = Callable[[str], str]
Runner = Callable[..., str]


class MigrateError(ValueError):
    pass


@dataclass(frozen=True)
class Import:
    clone: Any
    writer: str
    clock: Callable[[], float]
    redactor: Redactor | None = None
    runner: Runner = git_runner.run


def _text(lines: Sequence[Mapping[str, object]]) -> bytes:
    return "".join(
        json.dumps(line, sort_keys=True, ensure_ascii=False) + "\n" for line in lines
    ).encode("utf-8")


def ledger_events(ledger: Path | str) -> list:

    found, unreadable = events.read_events(ledger)
    if unreadable:
        first = unreadable[0]
        raise MigrateError(
            f"{len(unreadable)} ledger line(s) do not parse, first {first.path.name}:"
            f"{first.line_number}; run fsck on the ledger and repair it, then migrate again"
        )
    header = snapshot.read_header(snapshot.snapshot_path(ledger))
    if header is not None and header.event_count > len(found):
        raise MigrateError(
            f"{snapshot.SNAPSHOT_NAME} folded {header.event_count} events and the ledger "
            f"logs hold {len(found)}; restore the missing log files, then migrate again"
        )
    return events.canonical_order(found)


def _apply(value: object, rule: Redactor) -> object:
    if isinstance(value, str):
        return rule(value)
    if isinstance(value, dict):
        return {key: _apply(item, rule) for key, item in value.items()}
    if isinstance(value, list):
        return [_apply(item, rule) for item in value]
    return value


def _holder_key(event: Any) -> str | None:

    payload = event.payload
    if event.kind == events.KIND_FIELD and payload.get("name") == events.HOLDER_FIELD:
        key = "value"
    elif event.kind == events.KIND_CREATED and events.HOLDER_FIELD in payload:
        key = events.HOLDER_FIELD
    else:
        return None
    return key if isinstance(payload.get(key), str) else None


def scrub(event: Any, redactor: Redactor | None) -> Any:

    def rule(value: str) -> str:
        return redact.text(value if redactor is None else redactor(value))

    holder = _holder_key(event)
    payload = {
        key: redact.text(str(value)) if key == holder else _apply(value, rule)
        for key, value in event.payload.items()
    }
    return dataclasses.replace(event, actor=rule(event.actor), payload=payload)


def line_of(event: Any, writer: str, at: float) -> dict[str, object]:

    made = hashlib.sha256(f"{writer} {event.id}".encode()).hexdigest()[:32]
    return {
        "kind": vault_view.LEDGER_KIND,
        "record": event.record,
        vault_view.LEDGER_FIELD: json.loads(events.to_json(event)),
        "id": made,
        "writer": writer,
        "session": SESSION,
        "at": at,
    }


def digest(found: Sequence[Any], records: Collection[str]) -> str:

    folded = events.fold(found).records
    body = {
        name: snapshot.record_to_dict(folded[name]) if name in folded else None
        for name in sorted(records)
    }
    text = json.dumps(body, sort_keys=True, ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def view_events(valid: Sequence[Mapping[str, object]], want: Any) -> list:

    held = {
        name: state.holder
        for name, state in vault_view.vault_fold.fold(valid).records.items()
        if state.holder is not None
    }
    return vault_view.renumbered(vault_view.carried(valid, want), held)


def _header_bytes(writer: str) -> int:
    return len(_text([{"kind": vault_shapes.SEGMENT_KIND, "writer": writer}]))


def _refuse_a_second_import(clone: Any, clean: Sequence[Any]) -> None:

    held = {
        str(line[vault_view.LEDGER_FIELD].get("id"))
        for line in vault_clone.valid_events(clone)
        if line.get("kind") == vault_view.LEDGER_KIND
        and isinstance(line.get(vault_view.LEDGER_FIELD), dict)
    }
    again = [event.id for event in clean if event.id in held]
    if again:
        raise MigrateError(
            f"the vault already carries {len(again)} of the {len(clean)} events, first "
            f"{again[0]}; this source was migrated before, so nothing was pushed"
        )


def _chunks(plan: Import, head: str, lines: list, git: Runner) -> list[str]:

    header = _header_bytes(plan.writer)
    made: list[str] = []
    rest = list(lines)
    while rest:
        _path, old = vault_share._segment(git, head, plan.writer)
        room = COMMIT_BYTES - (len(old.encode("utf-8")) if old else header)
        take, size = 0, 0
        for line in rest:
            size += len(_text([line]))
            if size > room:
                break
            take += 1
        head = vault_share._build(plan.clone, head, plan.writer, rest[: max(take, 1)], git)
        made.append(head)
        rest = rest[max(take, 1) :]
    return made


def _refuse(name: str, given: tuple, made: tuple, outcome: str) -> None:
    if given[1] != made[1]:
        raise MigrateError(
            f"the {name} differs: {given[0]} {given[1]}, {made[0]} {made[1]}; {outcome}"
        )


def _verified(plan: Import, head: str, lines: list, source: list) -> dict[str, object]:

    def git(args: list[str], **options: Any) -> str:
        return plan.runner(args, plan.clone.bare, **options)

    made = _chunks(plan, head, lines, git) if lines else []
    tip = made[-1] if made else head
    verdicts = vault_shapes.check(plan.clone.bare, tip)
    landed = [
        event
        for verdict in verdicts
        if verdict.valid and verdict.commit in made
        for event in verdict.events
    ]
    _refuse("event count", ("ledger", len(lines)), ("vault", len(landed)), _UNPUSHED)
    records = {event.record for event in source}
    held = digest(vault_view.carried(lines, EVERY_PROJECT), records)
    shown = digest(view_events(vault_shapes.valid_events(verdicts), EVERY_PROJECT), records)
    _refuse("fold digest", ("ledger", held), ("vault view", shown), _UNPUSHED)
    return {"tip": tip, "commits": len(made), "events": len(lines), "digest": shown}


def to_vault(found: Sequence[Any], plan: Import) -> dict[str, object]:

    git_runner.writer_id(plan.writer)
    if not found:
        raise MigrateError("the source holds no events, so there is nothing to migrate")
    at = plan.clock()
    clean = [scrub(event, plan.redactor) for event in found]
    lines = [line_of(event, plan.writer, at) for event in clean]
    largest = max((len(_text([line])) for line in lines), default=0)
    if largest > COMMIT_BYTES - _header_bytes(plan.writer):
        raise MigrateError(
            f"one ledger event is {largest} bytes, above the {COMMIT_BYTES} byte commit limit"
        )
    for _attempt in range(vault_share.BULK_ATTEMPTS):
        fetched = vault_clone.fetch(plan.clone, clock=plan.clock, runner=plan.runner)
        _refuse_a_second_import(plan.clone, clean)
        shown = _verified(plan, str(fetched["head"]), lines, clean)
        try:
            vault_share._push(
                plan.clone, str(shown["tip"]), plan.runner, git_runner.DEFAULT_TIMEOUT
            )
        except git_runner.GitRunnerError:
            continue
        vault_clone.settle(plan.clone, runner=plan.runner)
        return {
            **shown,
            "redacted": sum(1 for index, old in enumerate(found) if clean[index] != old),
            "not_in_view": len(lines) - len(vault_view.carried(lines, EVERY_PROJECT)),
        }
    raise MigrateError(
        f"the vault refused the import push {vault_share.BULK_ATTEMPTS} times; nothing landed, "
        "so run migrate --to-vault again"
    )


def ledger_to_vault(ledger: Path | str, plan: Import) -> dict[str, object]:
    return to_vault(ledger_events(ledger), plan)


def backlog_to_vault(source: Any, plan: Import) -> dict[str, object]:

    read = beans.READERS[source.format](source.export, name=source.name)
    vault_clone.private(plan.clone.folder)
    with tempfile.TemporaryDirectory(dir=plan.clone.folder) as scratch:
        report = migrate.import_snapshot(scratch, read, clock=plan.clock)
        refused = [*report.rejected, *report.unreadable]
        if refused:
            raise MigrateError(
                f"{len(refused)} {source.format} record(s) were refused, first "
                f"{refused[0].subject}: {refused[0].reason}; the vault took nothing"
            )
        found = ledger_events(scratch)
    return {**to_vault(found, plan), "records": len(read.records)}


def from_vault(
    clone: Any,
    prefix: str,
    target: Path,
    *,
    clock: Callable[[], float],
    runner: Runner = git_runner.run,
) -> dict[str, object]:

    try:
        vault_view.ids.validate_prefix(prefix)
    except vault_view.ids.IdError as exc:
        raise MigrateError(f"the prefix cannot name a record: {exc}") from None
    if events.ledger_paths(target):
        raise MigrateError(f"{target} already holds ledger files; name an empty folder")
    vault_clone.fetch(clone, clock=clock, runner=runner)
    shown = view_events(vault_clone.valid_events(clone), vault_view.Want(prefix))
    text = "".join(events.to_json(event) + "\n" for event in shown)
    records = {event.record for event in shown}
    with tempfile.TemporaryDirectory(dir=clone.folder) as scratch:
        (Path(scratch) / events.INITIAL_LOG_NAME).write_text(text, encoding="utf-8")
        back = ledger_events(scratch)
    exported = ("vault view", len(shown)), ("ledger", len(back))
    _refuse("event count", *exported, _UNWRITTEN)
    exported = ("vault view", digest(shown, records)), ("ledger", digest(back, records))
    _refuse("fold digest", *exported, _UNWRITTEN)
    target.mkdir(parents=True, exist_ok=True)
    vault_clone.durable(target / events.INITIAL_LOG_NAME, text)
    if not (target / templates.TEMPLATE_FILE).exists():
        vault_clone.durable(target / templates.TEMPLATE_FILE, json.dumps({"prefix": prefix}) + "\n")
    return {"events": len(shown), "records": len(records), "digest": digest(back, records)}

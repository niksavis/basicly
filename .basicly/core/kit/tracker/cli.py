from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load(file_name: str, module_name: str) -> Any:

    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError(f"the tracker kit's {file_name} is missing from beside cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


snapshot = _load("snapshot.py", "basicly_tracker_kit_snapshot")
scheduler = _load("scheduler.py", "basicly_tracker_kit_scheduler")
commands = _load("commands.py", "basicly_tracker_kit_commands")
queries = _load("queries.py", "basicly_tracker_kit_queries")
events = snapshot.events
ids = events.ids

DEFAULT_STATUS = "open"

EXIT_OK = 0

EXIT_REFUSED = 1


def _field_value(raw: str) -> object:

    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _fields(title: str, pairs: Sequence[str]) -> dict[str, object]:

    fields: dict[str, object] = {}
    if title:
        fields[scheduler.TITLE_FIELD] = title
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name:
            raise ValueError(f"--field {pair!r} is not name=value")
        fields[name] = _field_value(raw)
    return fields


create_record = commands.create_root
query_records = queries.query_records


def read_record(directory: Path | str, record: str) -> dict[str, object] | None:

    states = queries.folded(directory)
    state = states.get(record)
    if state is None:
        return None
    views, _ = queries.views_and_children(directory)
    shown = snapshot.record_to_dict(state)
    shown.update(_edges(record, views, states))
    return shown


def _edges(record: str, views: Mapping[str, Any], states: Mapping[str, Any]) -> dict[str, object]:

    view = views.get(record)
    return {
        "dependencies": [
            {
                "id": edge.target,
                "dependency_type": edge.type,
                "status": _status(views, edge.target),
            }
            for edge in (view.dependencies if view is not None else ())
        ],
        "dependents": [
            {
                "id": other,
                "dependency_type": edge.type,
                "status": held.status or "",
                "title": str(states[other].fields.get("title", "")) if other in states else "",
            }
            for other, held in sorted(views.items())
            for edge in held.dependencies
            if edge.target == record and not held.tombstoned
        ],
    }


def _status(views: Mapping[str, Any], record: str) -> str:
    view = views.get(record)
    return "unknown" if view is None else view.status or ""


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Create, read, query and advance work items in a tracker kit ledger."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="mint a record id and append its first events")
    create.add_argument("directory", help=f"the ledger directory holding {events.LOG_GLOB}")
    create.add_argument("--prefix", required=True, help="the ledger's id prefix, e.g. acme")
    create.add_argument("--title", default="", help="the record's title")
    create.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="an extra field; the value is read as JSON when it parses, else as a string",
    )
    create.add_argument("--status", default=DEFAULT_STATUS, help="the status to open it at")

    show = sub.add_parser("show", help="read one record's folded state and both edge directions")
    show.add_argument("directory", help="the ledger directory")
    show.add_argument("record", help="the record id")

    listing = sub.add_parser("list", help="query the records the ledger holds")
    listing.add_argument("directory", help="the ledger directory")
    listing.add_argument("--status", default=None, help="only records at this status")
    listing.add_argument("--limit", type=int, default=None, help="at most this many records")

    _add_query_parsers(sub)
    _add_write_parsers(sub)
    return parser


def _add_query_parsers(sub: Any) -> None:
    for name, helping in (
        ("ready", "the ranked ready set: what can be worked on now"),
        ("blocked", "each dispatchable record that is not ready, and what holds it"),
        ("stats", "counts by status, plus the ready and blocked counts"),
    ):
        view = sub.add_parser(name, help=helping)
        view.add_argument("directory", help="the ledger directory")
        if name == "ready":
            view.add_argument("--limit", type=int, default=None, help="at most this many")


def _add_write_parsers(sub: Any) -> None:
    child = sub.add_parser("child", help="mint the next child id under a parent")
    child.add_argument("directory", help="the ledger directory")
    child.add_argument("parent", help="the parent record id")
    child.add_argument("--title", default="", help="the record's title")
    child.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")
    child.add_argument("--status", default=DEFAULT_STATUS, help="the status to open it at")

    update = sub.add_parser("update", help="set a record's fields, status or labels")
    update.add_argument("directory", help="the ledger directory")
    update.add_argument("record", help="the record id")
    update.add_argument("--field", action="append", default=[], metavar="NAME=VALUE")
    update.add_argument("--status", default="", help="the status to move it to")
    update.add_argument("--add-label", action="append", default=[], metavar="LABEL")
    update.add_argument("--remove-label", action="append", default=[], metavar="LABEL")

    closing = sub.add_parser("close", help="move records to the closed status")
    closing.add_argument("directory", help="the ledger directory")
    closing.add_argument("record", nargs="+", help="the record ids to close")
    closing.add_argument("--reason", default="", help="why, recorded as a field")

    note = sub.add_parser("comment", help="append one comment to a record")
    note.add_argument("directory", help="the ledger directory")
    note.add_argument("record", help="the record id")
    note.add_argument("text", help="the comment body")

    dep = sub.add_parser("dep", help="record a dependency edge on the dependent")
    dep.add_argument("directory", help="the ledger directory")
    dep.add_argument("record", help="the dependent record id")
    dep.add_argument("target", help="the record it depends on")
    dep.add_argument("--type", dest="edge_type", default="blocks", help="the edge type")

    removal = sub.add_parser("delete", help="tombstone a record; its id is never reused")
    removal.add_argument("directory", help="the ledger directory")
    removal.add_argument("record", help="the record id")


_WRITES: dict[str, Callable[[argparse.Namespace, Any], Sequence[Any]]] = {
    "child": lambda a, r: commands.create_child(
        a.directory, a.parent, _fields(a.title, a.field), status=a.status, redact=r
    ),
    "update": lambda a, r: commands.update(
        a.directory,
        a.record,
        fields=_fields("", a.field),
        status=a.status,
        add_labels=a.add_label,
        remove_labels=a.remove_label,
        redact=r,
    ),
    "close": lambda a, r: commands.close(a.directory, a.record, reason=a.reason, redact=r),
    "comment": lambda a, r: commands.comment(a.directory, a.record, a.text, redact=r),
    "dep": lambda a, r: commands.add_dependency(
        a.directory, a.record, a.target, edge_type=a.edge_type, redact=r
    ),
    "delete": lambda a, r: commands.delete(a.directory, a.record, redact=r),
}

_VIEWS: dict[str, Callable[[argparse.Namespace], dict[str, object]]] = {
    "ready": lambda a: queries.ready(a.directory, limit=a.limit),
    "blocked": lambda a: queries.blocked(a.directory),
    "stats": lambda a: queries.stats(a.directory),
}


def _run(
    args: argparse.Namespace, redact: Callable[[str], str] | None
) -> tuple[int, dict[str, object]]:
    if args.command == "create":
        written = create_record(
            args.directory,
            _fields(args.title, args.field),
            prefix=args.prefix,
            status=args.status,
            redact=redact,
        )
        return EXIT_OK, {"record": written[0].record, "events": [event.id for event in written]}
    if args.command == "show":
        found = read_record(args.directory, args.record)
        if found is None:
            return EXIT_REFUSED, {"record": args.record, "found": False}
        return EXIT_OK, found
    if (view := _VIEWS.get(args.command)) is not None:
        return EXIT_OK, view(args)
    if (write := _WRITES.get(args.command)) is not None:
        appended = write(args, redact)
        if appended:
            record = appended[0].record
        else:
            ids = args.record
            record = ids if isinstance(ids, str) else ids[0]
        return EXIT_OK, {
            "record": record,
            "events": [event.id for event in appended],
            "appended": bool(appended),
        }
    records = query_records(args.directory, status=args.status, limit=args.limit)
    return EXIT_OK, {"count": len(records), "records": records}


def main(argv: Sequence[str] | None = None, *, redact: Callable[[str], str] | None = None) -> int:

    args = _parser().parse_args(argv)
    report: Mapping[str, object]
    try:
        code, report = _run(args, redact)
    except events.LedgerError as exc:
        code, report = EXIT_REFUSED, {"refused": str(exc)}
    except ValueError as exc:
        code, report = EXIT_REFUSED, {"refused": str(exc)}
    print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

from basicly import __version__, owned_store, redact, tracker, ui

QUERIES_KIT_MODULE = "queries"
BOARD_KIT = "board"
BOARD_ENTRY = "server.py"
SERVE_COMMAND = "basicly tracker serve"
CONTRACT_VERSION = 1
ITEM_SOURCE = "basicly"
OTHER_STATUS = "other"
OPEN_STATUSES = ("open", "in_progress", "blocked", "deferred")
ITEM_STATUSES = (*OPEN_STATUSES, "closed", OTHER_STATUS)
WATCH = (owned_store.LEDGER_DIR / "*.jsonl").as_posix()
WRITES = (("tracker", "write"),)
RUNS_REPOSITORY_KIT = " (runs this repository's tracker kit code)"


class BoardKitMissingError(LookupError):
    pass


def _queries() -> Any:

    return owned_store.packaged_kit(QUERIES_KIT_MODULE)


def _report(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def ready_report(repo_root: Path, limit: int | None = None) -> dict[str, Any]:

    return _queries().ready(owned_store.present_ledger(repo_root), limit=limit)


def blocked_report(repo_root: Path) -> dict[str, Any]:
    return _queries().blocked(owned_store.present_ledger(repo_root))


def cmd_ready(args: argparse.Namespace) -> int:
    report = ready_report(Path.cwd(), getattr(args, "limit", None))
    if getattr(args, "json", False):
        _report(report)
        return 0
    ui.table(
        f"Ready ({report['count']}, {report['sort']})",
        ["rank", "score", "record", "title"],
        [
            [str(row["rank"]), str(row["score"]), str(row["record"]), str(row["title"])]
            for row in report["records"]
        ],
    )
    return 0


def cmd_blocked(args: argparse.Namespace) -> int:
    report = blocked_report(Path.cwd())
    if getattr(args, "json", False):
        _report(report)
        return 0
    ui.table(
        f"Blocked ({report['count']})",
        ["record", "status", "blocked by", "children"],
        [
            [
                str(row["record"]),
                str(row["status"]),
                ", ".join(f"{held['record']} ({held['status']})" for held in row["blocked_by"]),
                str(len(row["children"])) if row["children"] else "",
            ]
            for row in report["records"]
        ],
    )
    return 0


def cmd_stats(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    report = _queries().stats(owned_store.present_ledger(repo_root))
    if getattr(args, "json", False):
        _report(report)
        return 0
    rows = [[name, str(count)] for name, count in report["by_status"].items()]
    rows += [
        ["", ""],
        ["ready", str(report["ready"])],
        ["blocked", str(report["blocked"])],
        ["tombstoned", str(report["tombstoned"])],
    ]
    ui.table(f"Backlog ({report['records']} records)", ["status", "count"], rows)
    return 0


def cmd_show(args: argparse.Namespace) -> int:

    repo_root = Path.cwd()
    found = _queries().read_record(owned_store.present_ledger(repo_root), args.record)
    if found is None:
        _report({"record": args.record, "found": False})
        return 1
    found.update(tracker.record_edges(repo_root, args.record))
    _report(found)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    records = _queries().query_records(
        owned_store.present_ledger(repo_root),
        status=getattr(args, "status", None),
        limit=getattr(args, "limit", None),
    )
    _report({"count": len(records), "records": records})
    return 0


def status_map() -> dict[str, str]:

    return {
        status: status if status in ITEM_STATUSES else OTHER_STATUS
        for status in owned_store.packaged_kit("values").WRITABLE_STATUSES
    }


def _priority(value: object) -> int | None:
    if isinstance(value, int):
        return value
    text = str(value or "").strip()
    return int(text) if text.isdigit() else None


def _item(state: Any, statuses: Mapping[str, str]) -> dict[str, object]:
    fields = state.fields
    return {
        "id": state.record,
        "title": str(fields.get("title") or ""),
        "status": statuses.get(str(state.status), OTHER_STATUS),
        "rawStatus": state.status,
        "priority": _priority(fields.get("priority")),
        "type": fields.get("issue_type") or None,
        "assignee": fields.get("assignee") or None,
        "updatedAt": state.dates.get("updated") or None,
        "source": ITEM_SOURCE,
    }


def items_report(repo_root: Path, statuses: Sequence[str]) -> list[dict[str, object]]:

    mapping = status_map()
    states = (
        owned_store.packaged_kit("snapshot").load(owned_store.present_ledger(repo_root)).records
    )
    items = [_item(states[key], mapping) for key in sorted(states) if not states[key].tombstoned]
    return [item for item in items if item["status"] in statuses]


def cmd_items(args: argparse.Namespace) -> int:
    items = items_report(Path.cwd(), args.status or OPEN_STATUSES)
    print(json.dumps(items, ensure_ascii=False, separators=(",", ":")))
    return 0


def describe_report() -> dict[str, object]:
    return {
        "name": ITEM_SOURCE,
        "version": __version__,
        "contract": CONTRACT_VERSION,
        "watch": [WATCH],
        "writes": [list(prefix) for prefix in WRITES],
        "statusMap": status_map(),
    }


def cmd_describe(_args: argparse.Namespace) -> int:
    print(json.dumps(describe_report(), ensure_ascii=False, separators=(",", ":")))
    return 0


def board_kit(repo_root: Path) -> Any:

    source = repo_root / owned_store.KIT_TRACKER_DIR.parent / BOARD_KIT / BOARD_ENTRY
    spec = importlib.util.spec_from_file_location("basicly_board_kit_server", source)
    if not source.is_file() or spec is None or spec.loader is None:
        raise BoardKitMissingError(f"the board kit is not installed at {source.parent}")
    server = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(server)
    return server


def cmd_serve(args: argparse.Namespace) -> int:

    repo_root = Path.cwd()
    args.directory = str(owned_store.ledger_dir(repo_root))
    try:
        server = board_kit(repo_root)
    except BoardKitMissingError as missing:
        print(f"Error: {missing}", file=sys.stderr)
        return 1
    return int(server.run(args, redact.redact_committed))


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "ready": cmd_ready,
    "blocked": cmd_blocked,
    "stats": cmd_stats,
    "show": cmd_show,
    "list": cmd_list,
    "items": cmd_items,
    "describe": cmd_describe,
    "serve": cmd_serve,
}


def add_parsers(tracker_sub: Any) -> None:
    for name, helping in (
        ("ready", "The ranked ready set: what can be worked on now"),
        ("blocked", "Each dispatchable record that is not ready, and what holds it"),
        ("stats", "The backlog's totals: records by status, ready and blocked"),
    ):
        view = tracker_sub.add_parser(name, help=helping)
        view.add_argument("--json", action="store_true", help="Print JSON instead of a table")
        if name == "ready":
            view.add_argument("--limit", type=int, default=None, help="At most this many")

    show = tracker_sub.add_parser("show", help="Print one record's folded state")
    show.add_argument("record", help="The record id")

    listing = tracker_sub.add_parser("list", help="Print the records the ledger holds")
    listing.add_argument(
        "--status", action="append", default=None, help="Only records at this status; repeat it"
    )
    listing.add_argument("--limit", type=int, default=None, help="At most this many records")

    items = tracker_sub.add_parser(
        "items",
        help=f"Print each record as one work item (adapter contract v{CONTRACT_VERSION})",
    )
    items.add_argument("--json", action="store_true", required=True, help="Print JSON")
    items.add_argument(
        "--status",
        action="append",
        choices=ITEM_STATUSES,
        default=None,
        help=f"Only items at this status; repeat it for more (default: {', '.join(OPEN_STATUSES)})",
    )

    described = tracker_sub.add_parser(
        "describe", help=f"Print this tracker's adapter description (contract v{CONTRACT_VERSION})"
    )
    described.add_argument("--json", action="store_true", required=True, help="Print JSON")

    served = tracker_sub.add_parser(
        "serve", help="Serve the HTTP API and the board page" + RUNS_REPOSITORY_KIT
    )
    served.add_argument("--host", default="127.0.0.1", help="The address to bind")
    served.add_argument("--port", type=int, default=8765, help="The port to bind")
    served.add_argument("--web", default="", help="Serve your own page directory instead")
    served.set_defaults(relaunch=SERVE_COMMAND)

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from basicly import owned_store, tracker, ui

QUERIES_KIT_MODULE = "queries"


def _queries(repo_root: Path) -> Any:

    return owned_store.kit(repo_root, QUERIES_KIT_MODULE)


def _report(payload: object) -> None:
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def ready_report(repo_root: Path, limit: int | None = None) -> dict[str, Any]:

    return _queries(repo_root).ready(owned_store.ledger_dir(repo_root), limit=limit)


def blocked_report(repo_root: Path) -> dict[str, Any]:
    return _queries(repo_root).blocked(owned_store.ledger_dir(repo_root))


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
    report = _queries(repo_root).stats(owned_store.ledger_dir(repo_root))
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
    found = _queries(repo_root).read_record(owned_store.ledger_dir(repo_root), args.record)
    if found is None:
        _report({"record": args.record, "found": False})
        return 1
    found.update(tracker.record_edges(repo_root, args.record))
    _report(found)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    records = _queries(repo_root).query_records(
        owned_store.ledger_dir(repo_root),
        status=getattr(args, "status", None),
        limit=getattr(args, "limit", None),
    )
    _report({"count": len(records), "records": records})
    return 0


HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "ready": cmd_ready,
    "blocked": cmd_blocked,
    "stats": cmd_stats,
    "show": cmd_show,
    "list": cmd_list,
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
    listing.add_argument("--status", default=None, help="Only records at this status")
    listing.add_argument("--limit", type=int, default=None, help="At most this many records")

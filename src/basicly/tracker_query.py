"""``basicly tracker`` read verbs: what the backlog holds, and what to work on next.

The engine's half of the kit's ``queries`` module. Its whole responsibility is *routing
and printing*: the kit answers, and nothing here folds an event or ranks a record. The
boundary against :mod:`basicly.tracker_cutover` is read against write — that module makes
a write reach the store, and every verb here is read-only.

**Why the engine carries it at all**, given the kit's own CLI answers the same questions:
a consumer of this repository does not know where the ledger is, and reaching the kit by
path is what the handover had to spell out at every use. ``basicly tracker ready`` reads
the ledger through :func:`basicly.owned_store.ledger_dir`, so the location is a fact the
engine already holds rather than an argument a human retypes.
"""

from __future__ import annotations

import argparse
import json
from collections.abc import Callable
from pathlib import Path
from typing import Any

from basicly import owned_store, ui

# The kit module answering every verb here. Named rather than reached through the
# differential, for the reason `owned_store.SCHEDULER_KIT_MODULE` gives: it sits beside
# that module rather than under it.
QUERIES_KIT_MODULE = "queries"


def _queries(repo_root: Path) -> Any:
    """The installed kit's query module.

    Raises:
        TrackerDivergenceError: the kit is not installed. A hard failure: an empty answer
            would read as an empty backlog.
    """
    return owned_store.kit(repo_root, QUERIES_KIT_MODULE)


def _report(payload: object) -> None:
    """Print *payload* as the JSON a caller scripting this branches on."""
    print(json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False))


def cmd_ready(args: argparse.Namespace) -> int:
    """Print the ranked ready set — what can be worked on now, best first."""
    repo_root = Path.cwd()
    report = _queries(repo_root).ready(
        owned_store.ledger_dir(repo_root), limit=getattr(args, "limit", None)
    )
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
    """Print each dispatchable record that is not ready, and what holds it."""
    repo_root = Path.cwd()
    report = _queries(repo_root).blocked(owned_store.ledger_dir(repo_root))
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
    """Print the backlog's totals: records by status, and the ready and blocked counts."""
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
    """Print one record's folded state.

    Returns 1 for a record the ledger does not hold, and says so: ``found: false`` reads
    exactly like a record with no body when a caller keys on a field instead.
    """
    repo_root = Path.cwd()
    found = _queries(repo_root).read_record(owned_store.ledger_dir(repo_root), args.record)
    if found is None:
        _report({"record": args.record, "found": False})
        return 1
    _report(found)
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    """Print the records the ledger holds, optionally narrowed to one status."""
    repo_root = Path.cwd()
    records = _queries(repo_root).query_records(
        owned_store.ledger_dir(repo_root),
        status=getattr(args, "status", None),
        limit=getattr(args, "limit", None),
    )
    _report({"count": len(records), "records": records})
    return 0


# The read verbs, as the handler each one takes. A table rather than a chain of branches,
# the same shape as `mirror._MIRRORED_WRITES`: the read surface is what a reader checks
# against the documented commands.
HANDLERS: dict[str, Callable[[argparse.Namespace], int]] = {
    "ready": cmd_ready,
    "blocked": cmd_blocked,
    "stats": cmd_stats,
    "show": cmd_show,
    "list": cmd_list,
}


def add_parsers(tracker_sub: Any) -> None:
    """Register every read verb on the ``basicly tracker`` subparser."""
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

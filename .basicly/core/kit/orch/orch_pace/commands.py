from __future__ import annotations

import argparse
import json
import re
import time
from collections.abc import Callable
from pathlib import Path

from orch_lessons import log
from orch_lessons.log import RefusedError
from orch_relay import record
from orch_state import store

from orch_pace import plan, snapshot

Clock = Callable[[], int]
MOMENTS = ("start", "done")
SETUP = (
    "wrap the status line command in ~/.claude/settings.json as "
    "'orch-statusline-tap -- <your command>' (docs/design/usage-snapshot.md)"
)
START_MARK = "[pace start]"
DONE_MARK = "[pace done]"
START_NOTE = re.compile(
    r"^\[pace start\] five_hour=(?P<used>[0-9.]+) resets_at=(?P<resets>[0-9]+)(?: |$)"
)


def add_parsers(groups: argparse._SubParsersAction) -> None:
    pace = groups.add_parser("pace")
    actions = pace.add_subparsers(dest="action")
    note = actions.add_parser("note")
    note.add_argument("record")
    note.add_argument("moment", choices=MOMENTS)


def now_s() -> int:
    return int(time.time())


def fresh(
    environ: dict[str, str], now: int
) -> tuple[Path, dict[str, object], dict[str, snapshot.Window]]:
    target = snapshot.path(environ)
    if not target.is_file():
        raise RefusedError(f"no usage snapshot at {target}; {SETUP}")
    document = snapshot.load(target)
    if not isinstance(document, dict) or document.get("format") != snapshot.FORMAT:
        raise RefusedError(f"the usage snapshot at {target} is unreadable or not {snapshot.FORMAT}")
    if document.get("version") != snapshot.VERSION:
        raise RefusedError(
            f"the usage snapshot at {target} has version {document.get('version')!r}; "
            f"this orch reads version {snapshot.VERSION}"
        )
    windows = snapshot.windows(document)
    five = windows.get("five_hour")
    if five is None:
        raise RefusedError(f"the usage snapshot at {target} has no five_hour reading; {SETUP}")
    age = now - int(five["read_at"])  # type: ignore[call-overload]
    if age > plan.STALE_S:
        raise RefusedError(
            f"the usage snapshot at {target} is stale: the last five_hour reading is "
            f"{plan.span(age)} old, over {plan.STALE_S // 60} minutes; a status line refresh "
            "writes a new one, or set refreshInterval in the statusLine settings"
        )
    return target, document, windows


def show(environ: dict[str, str], now: int) -> str:
    target, document, windows = fresh(environ, now)
    paces = [plan.pace(name, windows[name], now) for name in snapshot.WINDOWS if name in windows]
    lines = [f"usage snapshot {target}, written by {document.get('writer')}"]
    lines += [plan.line(p, now) for p in paces]
    lines.append(f"advice: {plan.overall(paces)}")
    return "\n".join(lines)


def working(cwd: Path, environ: dict[str, str]) -> str:
    root = store.find(cwd, environ)
    if root is None:
        return ""
    count = sum(1 for s in store.sessions(root) if s.get("state") == "working")
    return f" working_sessions={count}"


def note(
    args: argparse.Namespace,
    cwd: Path,
    environ: dict[str, str],
    now: int,
    tracker: record.Tracker | None = None,
) -> str:
    _, _, windows = fresh(environ, now)
    five = windows["five_hour"]
    used, resets = float(five["used_percentage"]), int(five["resets_at"])  # type: ignore[arg-type, call-overload]
    found = tracker or record.Tracker(log.repo_root(cwd))
    head = f"five_hour={used:.1f} resets_at={resets}"
    if args.moment == "start":
        text = f"{START_MARK} {head}{working(cwd, environ)}"
        found.comment(args.record, text)
        return f"noted on {args.record}: {text}"
    shown = json.loads(found.runner([*found.base, "show", found.ledger, args.record]))
    notes = shown.get("comment_log", []) if isinstance(shown, dict) else []
    starts = [
        m
        for n in notes
        if isinstance(n, dict) and isinstance(n.get("text"), str)
        for m in [START_NOTE.match(n["text"])]
        if m is not None
    ]
    if not starts:
        raise RefusedError(
            f"{args.record} has no {START_MARK} note; run: orch pace note {args.record} start"
        )
    began = starts[-1]
    fields = shown.get("fields", {}) if isinstance(shown, dict) else {}
    kind = fields.get("issue_type", "") if isinstance(fields, dict) else ""
    same = abs(resets - int(began["resets"])) <= snapshot.CYCLE_APART_S
    change = f"{used - float(began['used']):.1f}" if same else "unknown_crossed_reset"
    text = f"{DONE_MARK} {head} change={change} type={kind or 'unknown'}{working(cwd, environ)}"
    found.comment(args.record, text)
    return f"noted on {args.record}: {text}"


def run(
    args: argparse.Namespace, cwd: Path, environ: dict[str, str], clock: Clock = now_s
) -> str | None:
    if args.group != "pace":
        return None
    if args.action == "note":
        return note(args, cwd, environ, clock())
    return show(environ, clock())

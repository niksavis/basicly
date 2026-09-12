from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from . import tracker, tracker_argv, ui

if TYPE_CHECKING:
    import argparse


def cmd_write(args: argparse.Namespace) -> int:
    argv = [arg for arg in (args.argv or []) if arg != "--"]
    if not argv:
        ui.say("tracker write: name a subcommand, e.g. `-- close b-1`")
        return 2
    if argv[0] == "create":
        record = tracker.create_record(Path.cwd(), argv)
        ui.say(json.dumps({"id": record}) if "--json" in argv else f"created: {record}")
        return 0
    if argv[0] == "close" and len(argv) > 1:
        _say_criteria(argv[1])
    return _record(argv)


def _record(argv: list[str]) -> int:

    try:
        receipt = tracker.write(Path.cwd(), argv)
    except RuntimeError as exc:
        ui.fail(f"not recorded: {exc}")
        if getattr(exc.__cause__, "retryable", False):
            ui.fail("  the ledger took no part of this write, so running it again is safe")
        return 1
    if receipt.landed:
        ui.say(f"recorded: {'; '.join(receipt.landed)}")
        if receipt.replayed:
            ui.say(f"  and {receipt.replayed} fact(s) the ledger already held")
        return 0
    ui.say(f"already recorded, so nothing was appended: {' '.join(argv)}")
    ui.say("  the ledger already holds this exact event; the record still reads as it did")
    ui.say(
        f"  if you mean to record it a second time, add {tracker_argv.REPEAT_FLAG} — which "
        f"appends once more every time it is run, and is not idempotent"
    )
    return 1


def _say_criteria(record: str) -> None:

    with contextlib.suppress(RuntimeError, ValueError, OSError):
        held = tracker.read_record(Path.cwd(), record) or {}
        criteria = str(held.get("acceptance_criteria") or "").strip()
        if criteria:
            ui.say(f"closing {record}, which asked for:")
            for line in criteria.splitlines():
                ui.say(f"  {line}")

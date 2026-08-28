"""``basicly tracker write`` — one hand-authored tracker write, through the engine seam.

The write half of ``basicly tracker``, against :mod:`basicly.tracker_query`'s read half.

**Why a human write has its own command.** Editing the log by hand appends events nothing
validated, against a store with no undo. Routing the edit through
:func:`basicly.tracker.write` applies the read-only guard, the argv classification and the
event translation to it (basicly-vkh0.24).
"""

from __future__ import annotations

import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

from . import tracker, tracker_argv, ui

if TYPE_CHECKING:
    import argparse


def cmd_write(args: argparse.Namespace) -> int:
    """Run one hand-authored tracker write through :func:`basicly.tracker.write`."""
    argv = [arg for arg in (args.argv or []) if arg != "--"]
    if not argv:
        ui.say("tracker write: name a subcommand, e.g. `-- close b-1`")
        return 2
    # The one write whose output the caller needs: the id the store minted (vkh0.29).
    # `--json` is honoured because the caller asked for it: printing prose to a caller
    # that passed `--json` is how a duplicate record got minted here — the id was piped
    # through `jq`, vanished, and the create was re-run (basicly-vkh0.42.10).
    if argv[0] == "create":
        record = tracker.create_record(Path.cwd(), argv)
        ui.say(json.dumps({"id": record}) if "--json" in argv else f"created: {record}")
        return 0
    if argv[0] == "close" and len(argv) > 1:
        _say_criteria(argv[1])
    if tracker.write(Path.cwd(), argv):
        ui.say(f"recorded: {' '.join(argv)}")
        return 0
    # Not `recorded:`, and not zero either. A write stating what the record's newest events
    # already say is skipped as a replay — and a caller reading success moves on, which cost
    # three commands and a wrong diagnosis (basicly-kn4rip). A script reads the exit code,
    # so the skip carries it too (basicly-bj8kks).
    ui.say(f"already recorded, so nothing was appended: {' '.join(argv)}")
    ui.say("  the ledger already holds this exact event; the record still reads as it did")
    ui.say(
        f"  if you mean to record it a second time, add {tracker_argv.REPEAT_FLAG} — which "
        f"appends once more every time it is run, and is not idempotent"
    )
    return 1


def _say_criteria(record: str) -> None:
    """Print what *record* asked for, before a hand close claims it was delivered.

    Not a refusal: a human may close for reasons the criteria never covered, and a gate
    that guessed which would be wrong more often than useful. `basicly-agzx.4` was closed
    here with three of four met and nothing said so.
    """
    with contextlib.suppress(RuntimeError, ValueError, OSError):
        held = tracker.read_record(Path.cwd(), record) or {}
        criteria = str(held.get("acceptance_criteria") or "").strip()
        if criteria:
            ui.say(f"closing {record}, which asked for:")
            for line in criteria.splitlines():
                ui.say(f"  {line}")

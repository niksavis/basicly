"""The tracker surface this repo actually uses, against the inventory of what exists.

One responsibility, and it is the census. ``br`` and ``bv`` ship several hundred
surfaces; the harness reaches a fraction of them, and the question that sizes the work
tracker's replacement is *which* fraction — a surface an engine path calls is a hard
requirement, one only a human at a prompt reaches can be served later or never
(``docs/requirements/work-tracker.md`` §6). This module joins the measured ledger against the
committed inventory and prints both halves, including the never-used set in full: a
truncated "and 26 more" would hide the actual scope decision.

Two side effects are opt-in flags rather than defaults, and both are refreshes of
recorded state rather than judgements about it: ``--refresh-surface`` re-probes the
binaries, ``--promote`` folds the spool into the committed ledger. Neither runs unless
asked, so the plain report is read-only.

Split out of ``usage_report`` when it crossed the module-size cap. The boundary is
*someone else's surface* against *our own*: :mod:`basicly.usage_report` reports what
this repo's own tools, skills and dispatches did, which is a different ledger and a
different question, so neither module imports the other.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from . import br, tracker_surface, tracker_usage, ui

if TYPE_CHECKING:
    import argparse


def _surface_class(row: tracker_usage.SurfaceRow) -> str:
    """Whether an engine path reaches *row*, or only a human at a prompt.

    The distinction is the one that sizes the replacement: the engine's set is a
    hard requirement, because a harness phase breaks without it, while an
    interactive-only surface can be served later or never
    (`docs/requirements/work-tracker.md` §6).
    """
    if row.engine_calls and row.interactive_calls:
        return "engine+interactive"
    if row.engine_calls:
        return "engine"
    return "interactive-only"


def _refresh_tracker_surface(repo_root: Path) -> None:
    """Re-probe br/bv for their full surface and rewrite the committed inventory."""
    br_path = br.which()
    if br_path is None:
        ui.say(
            "br is not on PATH, so the surface inventory cannot be refreshed. "
            "The report below uses the committed inventory unchanged.",
            style="warn",
        )
        return
    inventory = tracker_surface.discover(br_path)
    tracker_surface.save(repo_root, inventory)
    ui.say(
        f"Wrote {len(inventory['br']['commands'])} br surface(s) and "
        f"{len(inventory['bv']['flags'])} bv flag(s) to {tracker_surface.INVENTORY_FILE}.",
        style="ok",
    )


def _promote_tracker_spool(repo_root: Path) -> None:
    """Fold the spool into the committed ledger, reporting anything it refused."""
    moved, dropped = tracker_usage.promote(repo_root)
    ui.say(
        f"Promoted {moved} spooled record(s) into {tracker_usage.LEDGER_FILE}."
        if moved
        else "Nothing spooled to promote.",
        style="ok" if moved else "warn",
    )
    if dropped:
        ui.say(
            f"Discarded {dropped} spooled record(s) whose subcommand is not a "
            "surface (shell text recorded by an older recorder).",
            style="warn",
        )


def cmd_tracker(args: argparse.Namespace) -> int:
    """Report the measured br/bv surface Phase 6 freezes its scope from."""
    repo_root = Path.cwd()
    notes: list[str] = []

    if args.refresh_surface:
        _refresh_tracker_surface(repo_root)
    if args.promote:
        _promote_tracker_spool(repo_root)

    rows = tracker_usage.summarize(repo_root)
    if not rows:
        ui.say(
            f"No tracker usage recorded yet — run the harness, then read "
            f"{tracker_usage.LEDGER_FILE}.",
            style="warn",
        )
        return 0

    inventory = tracker_surface.load(repo_root)
    measured = {(row.binary, row.subcommand) for row in rows}
    unused: dict[str, list[str]] = {}
    unknown: list[tuple[str, str]] = []
    if inventory is None:
        notes.append(
            "No surface inventory committed, so the never-used set is unknown. "
            "Run `basicly usage tracker --refresh-surface`."
        )
    else:
        unused = tracker_surface.never_used(inventory, measured)
        unknown = tracker_surface.unknown_used(inventory, measured)

    if args.as_json:
        ui.say(
            json.dumps(
                {
                    "used": [
                        {
                            "binary": row.binary,
                            "subcommand": row.subcommand,
                            "calls": row.calls,
                            "engine_calls": row.engine_calls,
                            "interactive_calls": row.interactive_calls,
                            "surface_class": _surface_class(row),
                            "access": row.access,
                            "mean_ms": round(row.mean_ms, 1) if row.mean_ms is not None else None,
                            "flags": list(row.flags),
                        }
                        for row in rows
                    ],
                    "never_used": unused,
                    "used_but_not_in_inventory": [list(pair) for pair in unknown],
                    "calls_by_access": tracker_usage.access_ratio(rows),
                    "inventory_br_version": (inventory or {}).get("br", {}).get("version"),
                    "notes": notes,
                },
                indent=2,
                sort_keys=True,
            )
        )
        return 0

    ui.table(
        f"Measured br/bv surface ({len(rows)})",
        ["binary", "subcommand", "calls", "reached by", "access", "mean ms", "flags"],
        [
            [
                row.binary,
                row.subcommand,
                f"{row.calls} ({row.engine_calls}e/{row.interactive_calls}i)",
                _surface_class(row),
                row.access,
                f"{row.mean_ms:.0f}" if row.mean_ms is not None else "—",
                " ".join(row.flags) or "—",
            ]
            for row in rows
        ],
    )
    ratio = tracker_usage.access_ratio(rows)
    ui.say(
        "calls by access: " + ", ".join(f"{name}={count}" for name, count in sorted(ratio.items())),
    )

    for binary, surfaces in sorted(unused.items()):
        known = len(tracker_surface.known_surfaces(inventory or {}).get(binary, ()))
        if not surfaces:
            ui.say(f"{binary}: every one of its {known} known surfaces is used.", style="ok")
            continue
        # Printed in full, never truncated: this is the set Phase 6 gets to not
        # build, and a "and 26 more" would hide the actual scope decision.
        namespaces = sorted(tracker_surface.groups(inventory or {}) & set(surfaces))
        qualifier = (
            f" ({len(namespaces)} of them a group namespace rather than an operation)"
            if namespaces
            else ""
        )
        ui.say(
            f"{binary}: {len(surfaces)} of {known} surfaces never used{qualifier} — "
            + ", ".join(surfaces)
        )

    if unknown:
        ui.say(
            "used but absent from the inventory (recorder defect or br drift): "
            + ", ".join(f"{binary} {sub}" for binary, sub in unknown),
            style="warn",
        )
    for note in notes:
        ui.say(note, style="warn")
    return 0

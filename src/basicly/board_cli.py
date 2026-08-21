"""The ``basicly board`` command group: its grammar, its dispatch, and Mode A's artifact.

Extracted from `cli.py` because that module carries a frozen size ratchet and three parser
groups were queued behind it; `tracker_query.add_parsers` is the shape followed, so `cli.py`
keeps the registration call and the dispatch-table entry and nothing else.

**This is where the live-lock facts are read, and the layer is the reason.** The producer may
not call `supervise.read_holder` itself - the import would close
`supervise -> board_snapshot -> supervise`, since the supervisor emits a snapshot of its own.
This module sits above `supervise`, so it reads the lock and passes the facts down. The layer
above supplies the fact the layer below cannot honestly derive.

**Mode A writes two files, and the second is the point.** The page is for a human; the
snapshot beside it is the contract, and every other consumer - a foreign board, a kit-only
checkout, a script - reads that rather than scraping the HTML.
"""

from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from . import board_render, board_schema, board_snapshot, supervise, ui

# The sidecar the transcript names. Beside the page rather than derived from its stem, so two
# pages written to one directory do not each claim their own copy of one contract.
SNAPSHOT_NAME = "board-snapshot.json"

# The C1 freshness claim, frozen into the help text. The word `real-time` is absent by rule,
# not by accident: C1's own operative sentence is "displays the snapshot's age and never uses
# the word real-time", and a surface that spends the word to deny it has still spent it.
FRESHNESS = (
    "The board is as fresh as the producer that wrote its snapshot, and it always shows "
    "how old that snapshot is. In wall mode with a live supervisor it refreshes on the "
    "supervisor's 15-second tick."
)

_BYTES_PER_KB = 1024

# The loud-miss wording, spelled a second time because this module may not import `cli`, whose
# `_dispatch` owns it for every required subparser. The two are bound by
# `test_cli.py::test_a_registered_subcommand_with_no_handler_fails_loudly`, which derives its
# sites from the parser and so asserts this whole string through `cli.main` at this site too.
MISS = (
    "internal error: subcommand {name!r} is registered on the parser but has no handler "
    "\N{EM DASH} this is a bug in basicly, not in your invocation"
)


def _session_facts(repo_root: Path) -> board_snapshot.SessionFacts | None:
    """The supervisor lock's facts, or None where no lock names a root.

    None rather than a guessed root: the `session` section is then omitted and its panel says
    the producer did not emit it, which is true. A root invented here would be a claim about
    which pass is running, drawn on a wall.
    """
    held = supervise.read_holder(repo_root)
    if held is None or not held.root_issue:
        return None
    stale = held.age_s > supervise.STALE_AFTER_S
    return board_snapshot.SessionFacts(
        root_issue=held.root_issue,
        supervised=not stale,
        session_id=held.session_id or "",
        age_s=held.age_s,
        stale=stale,
    )


def _kb(path: Path) -> str:
    """*path*'s size as the transcript prints it."""
    return f"{path.stat().st_size / _BYTES_PER_KB:.0f} KB"


def cmd_validate(args: argparse.Namespace) -> int:
    """Report whether one snapshot is readable by this consumer, and exit on the answer.

    The whole verb, because the judgement is `board_schema`'s: a major-version mismatch
    is a different contract and refuses, an unknown key is reported and admitted.
    """
    verdict = board_schema.validate_file(Path.cwd(), args.path)
    ui.say(verdict.summary)
    return verdict.exit_code


def cmd_emit(args: argparse.Namespace) -> int:
    """Mode A: fold a snapshot, write the page and the contract beside it, say what it holds.

    A document the contract refuses is not drawn. Its verdict is printed and its exit code
    returned instead, because a page rendered over a document with no valid age on it is the
    one output this design has no honest use for.
    """
    if args.out is None:
        ui.warn("board: --out is required to write the page, or name a subcommand")
        return 2
    repo_root = Path.cwd()
    started = time.perf_counter()
    document = board_snapshot.build_document(
        repo_root, facts=board_snapshot.Facts(session=_session_facts(repo_root))
    )
    verdict = board_schema.verdict(repo_root, document)
    took = (time.perf_counter() - started) * 1000
    if not verdict.readable:
        ui.say(verdict.summary)
        return verdict.exit_code
    page = board_render.page(document, verdict, now=datetime.now(UTC))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    sidecar = args.out.parent / SNAPSHOT_NAME
    sidecar.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ui.say(f"board: {board_schema.VERSION} snapshot built in {took:.0f} ms")
    ui.say(
        f"board: sections  {len(verdict.renderable)} rendered, "
        f"{len(verdict.withheld)} withheld, {len(verdict.absent)} not emitted"
    )
    ui.say(f"board: wrote {args.out} (self-contained, {_kb(args.out)}) - open it in a browser")
    ui.say(f"board: wrote {sidecar} ({_kb(sidecar)}) - the contract, for any other consumer")
    return verdict.exit_code


_HANDLERS = {None: cmd_emit, "validate": cmd_validate}


def cmd_board(args: argparse.Namespace) -> int:
    """Dispatch the harness board's subcommands; no subcommand is Mode A's artifact.

    Its own dispatch rather than `cli._dispatch`, which documents that every subparser in that
    file is required. This group's is not - `basicly board --out X` is the documented Mode A
    invocation - so the None key is a registered route here and a miss is still loud.
    """
    chosen = getattr(args, "board_command", None)
    handler = _HANDLERS.get(chosen)
    if handler is None:
        name = f"board {chosen}".strip()
        ui.warn(MISS.format(name=name))
        return 2
    return handler(args)


def add_parsers(subparsers: argparse._SubParsersAction) -> None:
    """Register `basicly board` — the harness board's page, and its snapshot surface."""
    board = subparsers.add_parser(
        "board",
        help="The harness board: the factory and the tracker, on one page",
        description=f"Write the harness board as one self-contained HTML file. {FRESHNESS}",
    )
    board.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Write the page here, with {SNAPSHOT_NAME} beside it",
    )
    board_sub = board.add_subparsers(dest="board_command", required=False)
    validate = board_sub.add_parser(
        "validate", help="Check a board snapshot against the schema this consumer reads"
    )
    validate.add_argument("path", type=Path, help="The snapshot file to read")

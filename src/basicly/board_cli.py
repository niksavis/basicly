from __future__ import annotations

import argparse
import json
import time
from datetime import UTC, datetime
from pathlib import Path

from . import (
    board_actions,
    board_assets,
    board_backlog,
    board_bodies,
    board_facts,
    board_kanban,
    board_record,
    board_render,
    board_schema,
    board_serve,
    ui,
)

SNAPSHOT_NAME = "board-snapshot.json"

FRESHNESS = (
    "The board is as fresh as the producer that wrote its snapshot, and it always shows "
    "how old that snapshot is. In wall mode with a live supervisor it refreshes on the "
    "supervisor's 15-second tick."
)

NO_WRITES = (
    "The server acquires no lock and writes no file, so it blocks no gate and a board "
    "can never be the reason a landing failed. An action an operator submits is run by the "
    "basicly CLI, which writes what that command has always written; --no-actions removes "
    "the route entirely."
)

_BYTES_PER_KB = 1024

MISS = (
    "internal error: subcommand {name!r} is registered on the parser but has no handler "
    "\N{EM DASH} this is a bug in basicly, not in your invocation"
)


KANBAN_NAME = "loop.html"

BACKLOG_NAME = "backlog.html"


def _kb(path: Path) -> str:
    return f"{path.stat().st_size / _BYTES_PER_KB:.0f} KB"


def _write_records(
    document: dict[str, object],
    verdict: board_schema.SnapshotVerdict,
    out: Path,
    now: datetime,
    bodies: dict[str, str],
) -> tuple[int, int]:

    written = refused = 0
    for ident in board_record.ids(document):
        if not board_record.writable(ident):
            refused += 1
            continue
        filled = board_record.context(
            document,
            verdict,
            ident,
            now,
            page=board_record.PageFacts(
                back=f"../{out.name}",
                start_command=board_actions.start_command(board_record.start_form(document, ident)),
                body=bodies.get(ident, ""),
            ),
        )
        if filled is None:
            continue
        landing = out.parent / board_record.href(ident)
        landing.parent.mkdir(parents=True, exist_ok=True)
        landing.write_text(board_render.render_record(filled), encoding="utf-8")
        written += 1
    return written, refused


def cmd_validate(args: argparse.Namespace) -> int:

    verdict = board_schema.validate_file(Path.cwd(), args.path)
    ui.say(verdict.summary)
    return verdict.exit_code


def cmd_emit(args: argparse.Namespace) -> int:

    if args.out is None:
        ui.warn("board: --out is required to write the page, or name a subcommand")
        return 2
    repo_root = Path.cwd()
    started = time.perf_counter()
    document = board_facts.document(repo_root)
    verdict = board_schema.verdict(repo_root, document)
    took = (time.perf_counter() - started) * 1000
    if not verdict.readable:
        ui.say(verdict.summary)
        return verdict.exit_code
    now = datetime.now(UTC)
    page = board_render.page(document, verdict, now=now, viewport=(args.height, args.width))
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_text(page, encoding="utf-8")
    sidecar = args.out.parent / SNAPSHOT_NAME
    sidecar.write_text(json.dumps(document, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    ui.say(f"board: {board_schema.VERSION} snapshot built in {took:.0f} ms")
    ui.say(
        f"board: sections  {len(verdict.renderable)} rendered, "
        f"{len(verdict.withheld)} withheld, {len(verdict.absent)} not emitted"
    )
    ui.say(f"board: wrote {args.out} ({_kb(args.out)}) - open it in a browser")
    ui.say(f"board: wrote {sidecar} ({_kb(sidecar)}) - the contract, for any other consumer")
    loop_page = args.out.parent / KANBAN_NAME
    loop_page.write_text(
        board_render.render_kanban(
            board_kanban.context(document, verdict, now, back=args.out.name)
        ),
        encoding="utf-8",
    )
    ui.say(f"board: wrote {loop_page} ({_kb(loop_page)}) - a column per phase, naming its records")
    plan_page = args.out.parent / BACKLOG_NAME
    plan_page.write_text(
        board_render.render_backlog(
            board_backlog.context(document, verdict, now, back=args.out.name)
        ),
        encoding="utf-8",
    )
    ui.say(f"board: wrote {plan_page} ({_kb(plan_page)}) - every record, grouped by feature")
    for asset in board_assets.write_beside(board_render.root(), args.out.parent):
        ui.say(
            f"board: wrote {asset} ({_kb(asset)}) - bootstrap {board_assets.BOOTSTRAP_VERSION}, "
            "the vendored stylesheet the pages link"
        )
    written, refused = _write_records(
        document, verdict, args.out, now, board_bodies.bodies(repo_root)
    )
    denied = f", {refused} id(s) refused as a file name" if refused else ""
    ui.say(
        f"board: wrote {written} record pages under "
        f"{args.out.parent / board_record.HREF_DIR}{denied} - every id on the page links to one"
    )
    return verdict.exit_code


def cmd_serve(args: argparse.Namespace) -> int:

    root = Path.cwd()
    return board_serve.serve(
        root,
        port=args.port,
        refresh_s=args.refresh,
        build=lambda: board_facts.document(root),
        actions=not args.no_actions,
        host=args.bind,
    )


_HANDLERS = {None: cmd_emit, "serve": cmd_serve, "validate": cmd_validate}


def cmd_board(args: argparse.Namespace) -> int:

    chosen = getattr(args, "board_command", None)
    handler = _HANDLERS.get(chosen)
    if handler is None:
        name = f"board {chosen}".strip()
        ui.warn(MISS.format(name=name))
        return 2
    return handler(args)


def add_parsers(subparsers: argparse._SubParsersAction) -> None:
    board = subparsers.add_parser(
        "board",
        help="The harness board: the factory and the tracker, on one page",
        description=f"Write the harness board as HTML pages that fetch nothing. {FRESHNESS}",
    )
    board.add_argument(
        "--out",
        type=Path,
        default=None,
        help=f"Write the page here, with {SNAPSHOT_NAME} beside it",
    )
    board.add_argument(
        "--height",
        type=float,
        default=None,
        help="The wall's own CSS pixel height, so the reclaimed ready list draws as many rows "
        "as actually fit there instead of the conservative default for an unstated one "
        "(basicly-ffm2yp)",
    )
    board.add_argument(
        "--width",
        type=float,
        default=None,
        help="The wall's own CSS pixel width, alongside --height: chrome above the reclaimed "
        "list wraps more at a narrow width, so the row count reads this too where it is given",
    )
    board_sub = board.add_subparsers(dest="board_command", required=False)
    validate = board_sub.add_parser(
        "validate", help="Check a board snapshot against the schema this consumer reads"
    )
    validate.add_argument("path", type=Path, help="The snapshot file to read")
    serve = board_sub.add_parser(
        "serve",
        help=f"Serve the board on {board_serve.HOST} for a wall display",
        description=(
            f"Serve the harness board on {board_serve.HOST} only. Reads are GET; the one "
            f"POST route runs a `basicly` command an operator submitted. {FRESHNESS} "
            f"{NO_WRITES}"
        ),
    )
    serve.add_argument(
        "--port",
        type=int,
        default=board_serve.DEFAULT_PORT,
        help="The port to bind; 0 takes an ephemeral one and prints it",
    )
    serve.add_argument(
        "--bind",
        default=board_serve.HOST,
        help="A literal IPv4 interface address for a touch wall or a team display; "
        "the default stays the loopback, and a wildcard or a hostname is refused",
    )
    serve.add_argument(
        "--refresh",
        type=float,
        default=board_serve.DEFAULT_REFRESH_S,
        help="Seconds between folds where no supervisor is writing snapshots",
    )
    serve.add_argument(
        "--no-actions",
        action="store_true",
        help="Register no action route at all - the recommended flag for an unattended wall, "
        "because a screen anyone in the room can touch should not be able to kill a lane",
    )

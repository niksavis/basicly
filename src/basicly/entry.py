from __future__ import annotations

import argparse
import importlib
import sys
from collections.abc import Callable, Sequence

from . import __version__
from .schema import ValidationError

VERSION_FLAG = "--version"
TRACKER_COMMAND = "tracker"


def tolerate_narrow_consoles() -> None:

    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            reconfigure(errors="replace")


def line_buffer_stdout() -> None:

    reconfigure = getattr(sys.stdout, "reconfigure", None)
    if reconfigure is not None:
        reconfigure(line_buffering=True)


def guarded(run: Callable[[], int]) -> int:

    try:
        return run()
    except ValidationError as exc:
        print(f"Validation error: {exc}", file=sys.stderr)
        return 1
    except Exception as exc:  # noqa: BLE001 — process boundary, reported not swallowed
        print(f"Error: {exc}", file=sys.stderr)
        return 1


def _tracker_read(arguments: list[str]) -> Callable[[], int] | None:

    if len(arguments) < 2 or arguments[0] != TRACKER_COMMAND:
        return None
    importlib.import_module(f"{__package__}.config")
    tracker_query = importlib.import_module(f"{__package__}.tracker_query")
    if arguments[1] not in tracker_query.HANDLERS:
        return None
    parser = argparse.ArgumentParser(prog="basicly")
    commands = parser.add_subparsers(dest="command", required=True)
    tracker_sub = commands.add_parser(TRACKER_COMMAND).add_subparsers(
        dest="tracker_command", required=True
    )
    tracker_query.add_parsers(tracker_sub)

    def run() -> int:
        args = parser.parse_args(arguments)
        return tracker_query.HANDLERS[args.tracker_command](args)

    return run


def main(argv: Sequence[str] | None = None) -> int:
    arguments = list(sys.argv[1:] if argv is None else argv)
    if arguments == [VERSION_FLAG]:
        print(f"basicly {__version__}")
        return 0
    read = _tracker_read(arguments)
    if read is None:
        return importlib.import_module(f"{__package__}.cli").main(arguments)
    tolerate_narrow_consoles()
    line_buffer_stdout()
    return guarded(read)


if __name__ == "__main__":
    raise SystemExit(main())

r"""The tracker kit's standalone entry point: create a record, read one, query the set.

The responsibility is *the command line*; the boundary is against the modules it drives.
Nothing here folds, mints or writes a line — ``events`` appends, ``ids`` mints,
``snapshot`` reads. This module owns the argument surface, the order of the two writes a
create is, and the JSON a caller gets back.

**The gap it closes** (`work-tracker.md` §5, which records it as a named gap against §4's
promise of a kit consumable with zero basicly imports and nothing on PATH): the read side
had entry points, ``snapshot.main`` and ``fsck.main``; the write side had only ``basicly
tracker``, which is the engine, and ``ids.mint_root_id`` had no caller outside its tests.
A repository that copied the kit could read a ledger it had no way to create.

``create`` mints a root id and appends ``created`` then ``status``; ``show`` reads one
record through the snapshot; ``list`` queries the folded set by status. Three things are
absent because they are the graph rather than an entry point, and the engine still owns
them: children (``ids.next_child_id``), edges, and ranking (``scheduler.ranking``).

**Redaction stays injected.** §4.2 requires a redaction pass on every write and the kit
may not import ``basicly.redact``, so :func:`main` takes the callable as a keyword. A bare
command line passes none and the ledger holds what the operator typed — honest for a typed
title, wrong for agent output, which belongs on the engine's write path (``br.py``).

Kit rules (`.basicly/core/kit/README.md`): no basicly, standard library only, no network,
no subprocess, and syntax an interpreter older than this repo's 3.14 floor can parse —
hence one exception class per handler.
"""

from __future__ import annotations

import argparse
import importlib.util
import json
import sys
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent


def _load(file_name: str, module_name: str) -> Any:
    """Load a sibling kit module by path, without touching ``sys.path``.

    The cache lookup is the point rather than the speed: a second load mints a second
    ``Event`` class, and a frozen dataclass compares unequal across the two. Every loader
    in the kit uses these same ``basicly_tracker_kit_<module>`` names for that reason.

    Raises:
        ImportError: *file_name* is not beside this file.
    """
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError(f"the tracker kit's {file_name} is missing from beside cli.py")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


snapshot = _load("snapshot.py", "basicly_tracker_kit_snapshot")
scheduler = _load("scheduler.py", "basicly_tracker_kit_scheduler")
events = snapshot.events
ids = events.ids

# The status a record is created with when the caller names none — the kit's own
# vocabulary spells it (`differential.Vocabulary.known_statuses`). Not validated against
# that set on the way in: the vocabulary is configurable because a consumer's statuses are
# its own, so refusing one here would refuse the case it exists for.
DEFAULT_STATUS = "open"

EXIT_OK = 0

# Every refusal: an unknown record, a directory that is not a ledger, a ledger that will
# not take the write. One code, because the report says which — a caller scripting this
# branches on the JSON, and a shell caller only needs "did it happen".
EXIT_REFUSED = 1


def _field_value(raw: str) -> object:
    """One ``--field`` value: JSON when it parses, otherwise the literal string.

    ``priority=2`` has to reach the ledger as an integer or ``scheduler._priority``
    silently reads it as the default band, and ``labels=["a"]`` as a list. Falling back to
    the string is what keeps ``title=fix the parser`` from needing quotes.
    """
    try:
        return json.loads(raw)
    except ValueError:
        return raw


def _fields(title: str, pairs: Sequence[str]) -> dict[str, object]:
    """The ``created`` event's payload, from ``--title`` and the ``--field name=value`` list.

    The title's field name comes from :data:`scheduler.TITLE_FIELD` rather than a literal:
    R2 in `work-tracker.md` is exactly one spelling per field, and the scheduler is the
    module that declares this one.

    Raises:
        ValueError: a pair has no ``=``.
    """
    fields: dict[str, object] = {}
    if title:
        fields[scheduler.TITLE_FIELD] = title
    for pair in pairs:
        name, sep, raw = pair.partition("=")
        if not sep or not name:
            raise ValueError(f"--field {pair!r} is not name=value")
        fields[name] = _field_value(raw)
    return fields


def create_record(
    directory: Path | str,
    fields: Mapping[str, object],
    *,
    prefix: str,
    status: str = DEFAULT_STATUS,
    redact: Callable[[str], str] | None = None,
) -> list[Any]:
    """Mint a root id under *prefix* and append the record's first two events.

    The mint and the append are **one critical section**, held over the ledger's own lock
    and passed into :func:`events.append` as ``held_lock``: minting reads every id the
    ledger has ever held, so a second writer appending between the read and the write
    could be handed the same id.

    Two events rather than one, because status is its own kind: the ``created`` event
    carries the fields and the fold reads status only from a ``status`` event, so a record
    written without one folds to ``status: null`` and answers no query.

    Returns:
        The events written, ``created`` first. The record id is ``[0].record``.

    Raises:
        events.LockUnavailableError: another writer held the ledger. Retryable.
        ids.IdSpaceExhaustedError: no free id under *prefix*.
    """
    ledger = Path(directory)
    ledger.mkdir(parents=True, exist_ok=True)
    with events.LedgerLock(ledger) as lock:
        folded = events.fold(events.read_events(ledger)[0])
        record = ids.mint_root_id(
            prefix,
            ids.minted_ever(
                [key for key, state in folded.records.items() if not state.tombstoned],
                [key for key, state in folded.records.items() if state.tombstoned],
            ),
        )
        return events.append(
            ledger,
            [
                events.Draft(record, events.KIND_CREATED, dict(fields)),
                events.Draft(record, events.KIND_STATUS, {"status": status}),
            ],
            redact=redact,
            held_lock=lock,
        )


def _folded(directory: Path | str) -> dict[str, Any]:
    """Every record in the ledger at *directory*, folded.

    Raises:
        events.LedgerError: *directory* is not a directory. Refused rather than answered
            as an empty ledger: a mistyped path would otherwise read as "no such record",
            which is the same answer a correct path gives for a record that never existed.
    """
    ledger = Path(directory)
    if not ledger.is_dir():
        raise events.LedgerError(f"{ledger} is not a ledger directory")
    return snapshot.load(ledger).records


def read_record(directory: Path | str, record: str) -> dict[str, object] | None:
    """One record's folded state as JSON, or ``None`` when the ledger does not hold it."""
    state = _folded(directory).get(record)
    return None if state is None else snapshot.record_to_dict(state)


def query_records(
    directory: Path | str, *, status: str | None = None, limit: int | None = None
) -> list[dict[str, object]]:
    """The ledger's records in id order, optionally narrowed to one *status*.

    Tombstoned records are left out. They stay in the fold on purpose — a delete is an
    event, not a removal — but a query is asking what the tracker currently holds, and
    ``show`` is still the way to read one back.
    """
    records = _folded(directory)
    matched = [
        snapshot.record_to_dict(records[key])
        for key in sorted(records)
        if not records[key].tombstoned and (status is None or records[key].status == status)
    ]
    return matched if limit is None else matched[:limit]


def _parser() -> argparse.ArgumentParser:
    """The three subcommands, each taking the ledger directory as its first argument."""
    parser = argparse.ArgumentParser(
        description="Create, read and query work items in a tracker kit ledger."
    )
    sub = parser.add_subparsers(dest="command", required=True)

    create = sub.add_parser("create", help="mint a record id and append its first events")
    create.add_argument("directory", help=f"the ledger directory holding {events.LOG_GLOB}")
    create.add_argument("--prefix", required=True, help="the ledger's id prefix, e.g. acme")
    create.add_argument("--title", default="", help="the record's title")
    create.add_argument(
        "--field",
        action="append",
        default=[],
        metavar="NAME=VALUE",
        help="an extra field; the value is read as JSON when it parses, else as a string",
    )
    create.add_argument("--status", default=DEFAULT_STATUS, help="the status to open it at")

    show = sub.add_parser("show", help="read one record's folded state")
    show.add_argument("directory", help="the ledger directory")
    show.add_argument("record", help="the record id")

    listing = sub.add_parser("list", help="query the records the ledger holds")
    listing.add_argument("directory", help="the ledger directory")
    listing.add_argument("--status", default=None, help="only records at this status")
    listing.add_argument("--limit", type=int, default=None, help="at most this many records")
    return parser


def _run(
    args: argparse.Namespace, redact: Callable[[str], str] | None
) -> tuple[int, dict[str, object]]:
    """Execute one parsed command, returning its exit code and the report to print."""
    if args.command == "create":
        written = create_record(
            args.directory,
            _fields(args.title, args.field),
            prefix=args.prefix,
            status=args.status,
            redact=redact,
        )
        return EXIT_OK, {"record": written[0].record, "events": [event.id for event in written]}
    if args.command == "show":
        found = read_record(args.directory, args.record)
        if found is None:
            return EXIT_REFUSED, {"record": args.record, "found": False}
        return EXIT_OK, found
    records = query_records(args.directory, status=args.status, limit=args.limit)
    return EXIT_OK, {"count": len(records), "records": records}


def main(argv: Sequence[str] | None = None, *, redact: Callable[[str], str] | None = None) -> int:
    """Run one command and print its JSON report.

    Args:
        argv: The command line, defaulting to ``sys.argv[1:]``.
        redact: Applied to every string written, §4.2. Injected because the kit may not
            import the engine's redactor; a command line passes none.

    Returns:
        :data:`EXIT_OK`, or :data:`EXIT_REFUSED` with a ``refused`` or ``found: false``
        report. A refusal is printed rather than raised so the report is machine-readable
        on both paths.
    """
    args = _parser().parse_args(argv)
    report: Mapping[str, object]
    try:
        code, report = _run(args, redact)
    except events.LedgerError as exc:
        code, report = EXIT_REFUSED, {"refused": str(exc)}
    except ValueError as exc:
        # A bad `--field` pair, and every `ids.IdError` — that family subclasses
        # ValueError, so a second handler for it would be unreachable.
        code, report = EXIT_REFUSED, {"refused": str(exc)}
    print(json.dumps(report, sort_keys=True, indent=2, ensure_ascii=False))
    return code


if __name__ == "__main__":
    sys.exit(main())

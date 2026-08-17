r"""Fail when the owned ledger holds a record whose body it does not carry.

``migrate._plan_record`` writes a record's title, description, type, priority and
acceptance criteria onto its ``created`` event and nowhere else, and appends no second one.
So a record the ledger holds status, comment, edge and gate events for but **no** ``created``
event is one the owned store can log the work on and never say what the work *was* —
deleting the external store destroys the only copy. Measured 2026-08-17: 920 records, nine
of them bodyless (basicly-vkh0.41).

**Why a check beside the differential rather than a fourth query.** ``differential.QUERIES``
is ``(phase, ready, gates)``, and ``differential.RecordView`` omits a title and a
description deliberately, so an incidental byte difference between the two stores cannot be
reported as a disagreement about a verdict. That difference is real: ``br.scrub_export``
redacts the committed export's text and the live tracker's is unredacted, so comparing body
*content* across the two stores would manufacture disagreements. This defect is
**presence** — answerable from the owned ledger alone, with no redaction false positive to
rule out.

Run::

    uv run python .scripts/ledger_bodies.py
    uv run python .scripts/ledger_bodies.py --repo ../some-consumer
"""

from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

# Host layout rather than kit contract — the kit takes its directory as an argument and
# names no path — so both are literals here with `--repo`/`--ledger` to override, exactly
# as `kit_deployment.py` holds the same two.
KIT_DIR = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_DIR = Path(".basicly") / "ledger"

# The kit's own `sys.modules` name, so this load and the kit's own are one module: two loads
# mint two `Event` classes and an `isinstance` against the wrong one is false.
_DIFFERENTIAL_MODULE_NAME = "basicly_tracker_kit_differential"

_LABEL = "ledger-bodies"


class LedgerBodyError(Exception):
    """No kit to read the ledger with, so the check has no answer to give."""


@dataclass(frozen=True)
class Bodies:
    """What the ledger holds, and which of it has no body.

    Attributes:
        records: Every record the ledger holds an event for. Reported even on a pass: a
            population of zero and a population that all passed give the same verdict, and
            only one of them is evidence.
        bodyless: The records with no ``created`` event, sorted.
    """

    records: int
    bodyless: tuple[str, ...]


def load_kit(kit_dir: Path) -> Any:
    """Load the kit's ``differential.py`` by path, the way a consumer without basicly would.

    The differential rather than ``events.py``: it exposes ``read_ledger`` and carries
    ``events`` under it, so one load yields both the reader and the ``created`` kind rather
    than this file spelling either a second time.

    Raises:
        LedgerBodyError: the kit is not there, or does not import.
    """
    source = kit_dir / "differential.py"
    if not source.is_file():
        raise LedgerBodyError(f"no tracker kit at {kit_dir.as_posix()} — nothing to check")
    spec = importlib.util.spec_from_file_location(_DIFFERENTIAL_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise LedgerBodyError(f"{source.as_posix()} is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_DIFFERENTIAL_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise LedgerBodyError(f"{source.as_posix()} did not import: {exc}") from exc
    return module


def measure(kit: Any, ledger: Path) -> Bodies:
    """Which records the ledger at *ledger* holds no ``created`` event for.

    Read off the raw events rather than off ``events.fold``, and that is the whole point:
    the fold carries a record's status and comments whether or not a body ever arrived, so
    it is precisely the reader that cannot see this gap.

    A ledger with no log files answers zero records rather than raising; :class:`Bodies`
    carries the count so that cannot read as a pass.
    """
    found = kit.read_ledger(ledger)
    held = {event.record for event in found}
    bodied = {event.record for event in found if event.kind == kit.events.KIND_CREATED}
    return Bodies(records=len(held), bodyless=tuple(sorted(held - bodied)))


def report(bodies: Bodies, ledger: Path) -> None:
    """Name every bodyless record, then the only repair an append-only log allows.

    The remedy names a mechanism rather than a command because no command reaches these
    records: ``basicly tracker adopt`` selects ``live ids - ledger record ids``, which
    subtracts a record the ledger holds events for however empty its body is (verified
    2026-08-17 against `br.adopt_hand_writes`).
    """
    for record in bodies.bodyless:
        print(
            f"{_LABEL}: {record}: no created event, so the ledger carries none of its "
            f"title, description, type, priority or acceptance criteria",
            file=sys.stderr,
        )
    print(
        f"{_LABEL}: {len(bodies.bodyless)} of {bodies.records} record(s) in "
        f"{ledger.as_posix()} have no body; deleting the external store would destroy the "
        f"only copy",
        file=sys.stderr,
    )
    print(
        f"{_LABEL}:   append a corrective created event per record from the committed "
        f"export, under `baseline.ADOPTION_SOURCE` (basicly-vkh0.41)",
        file=sys.stderr,
    )


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point: report every record the owned ledger holds and cannot describe."""
    parser = argparse.ArgumentParser(
        description="Check that the owned ledger carries a body for every record it holds."
    )
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="the host repository's root (default: this script's repository)",
    )
    parser.add_argument(
        "--ledger",
        type=Path,
        default=LEDGER_DIR,
        help=f"the ledger directory, relative to --repo (default: {LEDGER_DIR.as_posix()})",
    )
    args = parser.parse_args(argv)

    try:
        kit = load_kit(args.repo / KIT_DIR)
        bodies = measure(kit, args.repo / args.ledger)
    except LedgerBodyError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    if bodies.bodyless:
        report(bodies, args.ledger)
        return 1
    print(f"{_LABEL}: all {bodies.records} record(s) in {args.ledger.as_posix()} carry a body")
    return 0


if __name__ == "__main__":
    sys.exit(main())

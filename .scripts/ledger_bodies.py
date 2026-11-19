from __future__ import annotations

import argparse
import importlib.util
import sys
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent


def _sibling(name: str) -> ModuleType:
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, (REPO_ROOT / ".scripts") / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"{name}.py is missing beside this script")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


frozen_ledger = _sibling("frozen_ledger")

KIT_DIR = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_DIR = Path(".basicly") / "ledger"

_DIFFERENTIAL_MODULE_NAME = "basicly_tracker_kit_differential"

_LABEL = "ledger-bodies"


class LedgerBodyError(Exception):
    pass


@dataclass(frozen=True)
class Bodies:
    records: int
    bodyless: tuple[str, ...]


def load_kit(kit_dir: Path) -> Any:

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

    found = kit.read_ledger(ledger)
    held = {event.record for event in found}
    bodied = {event.record for event in found if event.kind == kit.events.KIND_CREATED}
    return Bodies(records=len(held), bodyless=tuple(sorted(held - bodied)))


def measure_frozen(kit: Any, repo: Path) -> Bodies:

    created = kit.events.KIND_CREATED
    archive = frozen_ledger.archive(repo)
    read = frozen_ledger.vault_events(repo)
    print(f"{_LABEL}: {read.note}")
    if read.events:
        held = {str(event.get("record")) for event in read.events}
        bodied = {str(e.get("record")) for e in read.events if e.get("kind") == created}
        return Bodies(records=len(held), bodyless=tuple(sorted(held - bodied)))
    return measure(kit, archive)


def report(bodies: Bodies, ledger: Path) -> None:

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
        ledger = args.repo / args.ledger
        frozen = ledger.is_file() and frozen_ledger.frozen(args.repo)
        bodies = measure_frozen(kit, args.repo) if frozen else measure(kit, ledger)
    except (LedgerBodyError, frozen_ledger.FrozenLedgerError) as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    if frozen and not bodies.records:
        print(
            f"{_LABEL}: the ledger is frozen and neither the archive nor the vault clone "
            f"held a record; nothing was checked. Restore {frozen_ledger.ARCHIVE.as_posix()}",
            file=sys.stderr,
        )
        return 1

    if bodies.bodyless:
        report(bodies, args.ledger)
        return 1
    print(f"{_LABEL}: all {bodies.records} record(s) in {args.ledger.as_posix()} carry a body")
    return 0


if __name__ == "__main__":
    sys.exit(main())

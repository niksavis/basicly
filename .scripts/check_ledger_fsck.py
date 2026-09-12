from __future__ import annotations

import argparse
import importlib.util
import sys
import tomllib
from collections.abc import Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parent.parent

KIT_DIR = Path(".basicly") / "core" / "kit" / "tracker"
LEDGER_DIR = Path(".basicly") / "ledger"

_FSCK_MODULE_NAME = "basicly_tracker_kit_fsck"
_GATE = "ledger_fsck"
_LABEL = "ledger-fsck"
FROZEN_TABLE = f"[tool.{_GATE}.frozen]"


class LedgerFsckError(Exception):
    pass


@dataclass(frozen=True)
class Counted:
    broken: dict[str, int]
    derived: tuple[str, ...]
    warnings: tuple[str, ...]
    events: int
    records: int
    unattributed: int


def load_kit(kit_dir: Path) -> Any:

    source = kit_dir / "fsck.py"
    if not source.is_file():
        raise LedgerFsckError(f"no tracker kit at {kit_dir.as_posix()} — nothing to check")
    spec = importlib.util.spec_from_file_location(_FSCK_MODULE_NAME, source)
    if spec is None or spec.loader is None:
        raise LedgerFsckError(f"{source.as_posix()} is not importable")
    module = importlib.util.module_from_spec(spec)
    sys.modules[_FSCK_MODULE_NAME] = module
    try:
        spec.loader.exec_module(module)
    except ImportError as exc:
        raise LedgerFsckError(f"{source.as_posix()} did not import: {exc}") from exc
    return module


def load_frozen(repo: Path) -> dict[str, int]:

    try:
        data = tomllib.loads((repo / "pyproject.toml").read_text(encoding="utf-8"))
    except (OSError, tomllib.TOMLDecodeError) as exc:
        raise LedgerFsckError(f"could not read pyproject.toml: {exc}") from exc
    table = data.get("tool", {}).get(_GATE)
    if not isinstance(table, dict) or not isinstance(table.get("frozen"), dict):
        raise LedgerFsckError(f"no {FROZEN_TABLE} in pyproject.toml")
    frozen = table["frozen"]
    if not all(isinstance(value, int) for value in frozen.values()):
        raise LedgerFsckError(f"{FROZEN_TABLE} must map each subject/kind to a count")
    return frozen


def measure(kit: Any, ledger: Path) -> Counted:
    report = kit.check(ledger)
    broken: dict[str, int] = {}
    derived: list[str] = []
    warnings: list[str] = []
    for finding in report.findings:
        line = f"{finding.subject}: {finding.kind}: {finding.detail}"
        if finding.severity == kit.BROKEN:
            key = f"{finding.subject}/{finding.kind}"
            broken[key] = broken.get(key, 0) + 1
        elif finding.severity == kit.DERIVED:
            derived.append(line)
        else:
            warnings.append(line)
    return Counted(
        broken=broken,
        derived=tuple(sorted(derived)),
        warnings=tuple(sorted(warnings)),
        events=report.events,
        records=report.records,
        unattributed=report.unattributed,
    )


def verdicts(counted: Counted, frozen: dict[str, int]) -> list[str]:
    rebuild = f"uv run python {(KIT_DIR / 'fsck.py').as_posix()} {LEDGER_DIR.as_posix()} --rebuild"
    found = [f"{line} — rebuild it: `{rebuild}`" for line in counted.derived]
    for key, count in sorted(counted.broken.items()):
        baseline = frozen.get(key)
        if baseline is None:
            found.append(
                f"{key}: {count} broken finding(s), recorded in no baseline — a defect in an "
                f"append-only log is repaired by a corrective event, never by an edit; record "
                f"it in {FROZEN_TABLE} only once it is proved unrepairable"
            )
        elif count > baseline:
            found.append(
                f"{key}: {count} broken finding(s), up from the frozen {baseline} — the "
                f"recorded defect grew"
            )
    for key, baseline in sorted(frozen.items()):
        count = counted.broken.get(key, 0)
        if count < baseline:
            found.append(
                f"{key}: {count} broken finding(s), down from the frozen {baseline} — bank it "
                f"by lowering the entry in {FROZEN_TABLE}, or delete an entry that reached zero"
            )
    return found


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Check the owned ledger with the kit's fsck against a recorded baseline."
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
        frozen = load_frozen(args.repo)
        counted = measure(kit, args.repo / args.ledger)
    except LedgerFsckError as exc:
        print(f"{_LABEL}: {exc}", file=sys.stderr)
        return 1

    for line in counted.warnings:
        print(f"{_LABEL}: {line}")
    found = verdicts(counted, frozen)
    if found:
        for line in found:
            print(f"{_LABEL}: {line}", file=sys.stderr)
        return 1
    recorded = sum(counted.broken.values())
    print(
        f"{_LABEL}: {counted.events} event(s) over {counted.records} record(s) in "
        f"{args.ledger.as_posix()}; {recorded} recorded defect(s), no new one, "
        f"{len(counted.warnings)} warning(s), {counted.unattributed} event(s) with no actor"
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())

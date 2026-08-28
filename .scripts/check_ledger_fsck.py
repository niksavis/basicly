r"""Fail when the owned ledger's `fsck` finds a defect that is not already recorded.

`fsck` is the only reader that checks the log against itself — forked and gapped sequence
chains, unparseable and malformed lines, edges into nothing, carried totals the fold
disagrees with, derived files that lie. It was not in `[[verify.checks]]`, so the one
`broken` finding this ledger carries was visible only to somebody who ran it by hand, and
nobody had since 2026-08-16 (basicly-t10ipy).

**Why an allowance rather than a plain pass/fail.** `ledger_bodies.py` was wired only after
its backfill landed, on the rule that a gate refusing the repository's own state fails every
commit and is worth nothing. That repair is not available here: the finding is a **lost
event**, and an append-only log has no undelete, so the condition can never be cleared. The
recorded finding is therefore declared in ``[tool.ledger_fsck.frozen]`` and the gate binds on
everything else — the same shape `check_docs_citations.py` uses, and for the same reason.

**The key is ``<subject>/<kind>``, not the subject.** A per-record allowance would absorb any
*other* defect that later landed on the same record, which is the fail-open a discriminator
exists to prevent. A count that falls has to be banked in the same diff, for the reason
``[tool.module_size.frozen]`` states: leaving the higher number licenses regrowth for free.

Warnings are printed and never fatal, which is `fsck`'s own rule
(.basicly/core/kit/tracker/SPEC.md §4.5) — an unfolded kind is a newer writer, not
corruption.

Run::

    uv run python .scripts/check_ledger_fsck.py
    uv run python .scripts/check_ledger_fsck.py --repo ../some-consumer
"""

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
    """The gate could not reach an answer: no kit to check with, or no baseline to read."""


@dataclass(frozen=True)
class Counted:
    """One `fsck` run, reduced to what the baseline is written in terms of.

    Attributes:
        broken: ``<subject>/<kind>`` to how many findings of that class it carries.
        derived: Findings a `fsck --rebuild` would clear, as printable lines.
        warnings: Findings that are reported and never fatal, as printable lines.
        events: Events the run folded, so a pass over an empty ledger is not silent.
        records: Records it folded, for the same reason.
        unattributed: Events among them naming no actor. Reported and never failed: the
            population is inherited and an append-only log has no way to attribute a write
            already made, so a gate on it would refuse every commit forever.
    """

    broken: dict[str, int]
    derived: tuple[str, ...]
    warnings: tuple[str, ...]
    events: int
    records: int
    unattributed: int


def load_kit(kit_dir: Path) -> Any:
    """Load the kit's ``fsck.py`` by path, the way a consumer without basicly would.

    Raises:
        LedgerFsckError: the kit is not there, or does not import.
    """
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
    """The recorded findings, from ``pyproject.toml``.

    Refused rather than defaulted when absent: an empty baseline would pass a ledger whose
    every recorded defect had been forgotten, which is the fail-open this gate replaces.

    Raises:
        LedgerFsckError: the table is missing or does not map each key to a count.
    """
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
    """Run `fsck` over *ledger* and reduce its report to counts per ``<subject>/<kind>``."""
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
    """Every way *counted* disagrees with *frozen*, each naming its own repair."""
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
    """Entry point: report every ledger defect the recorded baseline does not already hold."""
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

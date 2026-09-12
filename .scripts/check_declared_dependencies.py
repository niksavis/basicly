from __future__ import annotations

import argparse
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO_ROOT / "src"))

from basicly import plan_record, tracker  # noqa: E402 - reachable after the path insert

_LABEL = "declared-dependencies"
_BLOCKS = "blocks"
_OPEN = "open"


@dataclass(frozen=True)
class Miss:
    record: str
    declared: str
    candidates: tuple[str, ...]
    edges: tuple[str, ...]

    def line(self) -> str:
        held = ", ".join(self.edges) or "none"
        if not self.candidates:
            named = f"`{self.declared}`, which names no record in the tracker"
        elif self.candidates == (self.declared,):
            named = f"`{self.declared}`"
        else:
            named = f"`{self.declared}`, a title held by {', '.join(self.candidates)}"
        return f"{self.record} declares {named} with no blocks edge behind it; edges held: {held}"


@dataclass(frozen=True)
class Reconciliation:
    open_records: int
    declaring: int
    declarations: int
    edges: int
    misses: tuple[Miss, ...]


def blocks_targets(record: Mapping[str, object]) -> tuple[str, ...]:

    rows = record.get("dependencies")
    if not isinstance(rows, list):
        return ()
    edges = (tracker.dependency_edge(row) for row in rows)
    return tuple(edge[0] for edge in edges if edge is not None and edge[1] == _BLOCKS)


def titles_to_ids(records: Sequence[Mapping[str, object]]) -> dict[str, tuple[str, ...]]:

    held: dict[str, list[str]] = {}
    for record in records:
        title = str(record.get("title") or "")
        if title:
            held.setdefault(title, []).append(str(record.get("id") or ""))
    return {title: tuple(ids) for title, ids in held.items()}


def candidates(
    declared: str, ids: frozenset[str], titles: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...]:

    if declared in ids:
        return (declared,)
    return titles.get(declared, ())


def reconcile(records: Sequence[Mapping[str, object]]) -> Reconciliation:
    ids = frozenset(str(record.get("id") or "") for record in records)
    titles = titles_to_ids(records)
    open_records = [record for record in records if record.get("status") == _OPEN]
    declaring = 0
    declarations = 0
    edge_count = 0
    misses: list[Miss] = []
    for record in open_records:
        plan = plan_record.parse_plan_section(str(record.get("description") or ""))
        edges = blocks_targets(record)
        edge_count += len(edges)
        if plan.depends_on is None:
            continue
        declaring += 1
        declarations += len(plan.depends_on)
        for declared in plan.depends_on:
            named = candidates(declared, ids, titles)
            if not any(target in edges for target in named):
                misses.append(
                    Miss(str(record.get("id") or ""), declared, named, tuple(sorted(edges)))
                )
    return Reconciliation(len(open_records), declaring, declarations, edge_count, tuple(misses))


def verdicts(found: Reconciliation) -> list[str]:
    if not found.open_records:
        return [
            "no open record was read at all — the ledger did not load, or this is not a "
            "repository with one; a check over an empty population proves nothing"
        ]
    if not found.declarations:
        return [
            f"{found.open_records} open record(s), {found.declaring} carrying a "
            f"`- depends on:` line and none of them naming a dependency — nothing was "
            f"reconciled, so this run is the probe failing rather than the tree agreeing"
        ]
    return [
        miss.line() for miss in sorted(found.misses, key=lambda miss: (miss.record, miss.declared))
    ]


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Reconcile declared dependencies with edges.")
    parser.add_argument(
        "--repo",
        type=Path,
        default=REPO_ROOT,
        help="the repository whose ledger to read (default: this script's repository)",
    )
    args = parser.parse_args(argv)

    found = reconcile(tracker.all_records(args.repo))
    faults = verdicts(found)
    if faults:
        disagreed = bool(found.misses)
        headline = (
            f"{len(faults)} disagreement(s) between a body and the graph"
            if disagreed
            else "an empty population, which is not an agreement"
        )
        print(f"{_LABEL}: {headline}")
        for fault in faults:
            print(f"  {fault}", file=sys.stderr)
        if disagreed:
            print(
                "  fix: correct the body's `- depends on:` line, or record the edge it names "
                "(`basicly tracker write -- dep add <record> <target> -t blocks`)",
                file=sys.stderr,
            )
        return 1
    print(
        f"{_LABEL}: {found.declarations} declared dependency(ies) reconciled across "
        f"{found.declaring} of {found.open_records} open record(s) carrying a "
        f"`- depends on:` line; {found.edges} blocks edge(s) over that population"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

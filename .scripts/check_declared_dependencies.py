"""Fail when a record's declared `- depends on:` disagrees with the `blocks` edges it holds.

Two sources hold one dependency. :func:`~basicly.plan_record.parse_plan_section` reads the
declaration off the body; the ledger's ``edge`` events hold the graph the scheduler ranks
with. The decomposer writes both from one plan, so they agree at creation and can drift on
any later hand edit or `dep remove` — and an **inverted** edge reads as correct from either
side: the body says this lane depends on its sibling, the ready set says the sibling depends
on this lane, and nothing reported the disagreement. `basicly-rn0o.4` sat exactly there, the
one board lane no supervised pass could dispatch (basicly-9yyj6i). That instance was
hand-corrected before this landed, which is the argument for the gate rather than against
it: the repair was a tracker write nothing would have caught twice.

**The declaration is checked against the edges, never the reverse.** An edge with no matching
declaration is ordinary — `dep add` is how a coupling gets recorded after the body was
written, and a `## Plan` is a plan rather than a mirror of the graph. Only the direction that
misleads a reader is a defect: a body naming a dependency the ready set does not enforce.

**A title resolves before a miss is reported.** `decompose` couples siblings by *title* (its
plan graph is title-keyed), so a body may legitimately name a title where the edge names the
id. Two of the eight live declarations are of that shape; reading ids alone would report both
as defects.

**An empty population is the probe failing, not the tree passing.** A parser that stopped
matching the recorded form, or a ledger that would not load, both answer "no disagreement" —
so a population with nothing in it to reconcile exits non-zero and says which half is empty.

Measured over this ledger on 2026-08-21: 232 open records, 26 carrying a `- depends on:`
line, 8 of those naming something, 110 `blocks` edges across them, and every declaration
reconciled. There is no go-live debt, so this binds hard rather than against a frozen
baseline.

Run::

    uv run python .scripts/check_declared_dependencies.py
    uv run python .scripts/check_declared_dependencies.py --repo ../some-consumer
"""

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
    """One declared dependency with no `blocks` edge behind it.

    Attributes:
        record: The record whose body declares it.
        declared: What the body says, verbatim — an id or a title.
        candidates: The ids *declared* names: itself when it is one, every record carrying
            it when it is a title, and none when the tracker holds neither.
        edges: The `blocks` edge targets the record does hold.
    """

    record: str
    declared: str
    candidates: tuple[str, ...]
    edges: tuple[str, ...]

    def line(self) -> str:
        """The finding as one printable line: the record, the declaration and its edges."""
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
    """What one run covered, and every disagreement in it.

    Attributes:
        open_records: The population read.
        declaring: Bodies carrying a `- depends on:` line, a declared-empty one included.
        declarations: Declared dependencies naming something — what was reconciled.
        edges: `blocks` edges held across the population.
        misses: One entry per declaration with no edge behind it.
    """

    open_records: int
    declaring: int
    declarations: int
    edges: int
    misses: tuple[Miss, ...]


def blocks_targets(record: Mapping[str, object]) -> tuple[str, ...]:
    """*record*'s outgoing `blocks` edge targets.

    Read through :func:`~basicly.tracker.dependency_edge` rather than the keys directly: it
    is the one reader for both spellings of an edge row, and a second spelling here would
    silently report every edge as absent.
    """
    rows = record.get("dependencies")
    if not isinstance(rows, list):
        return ()
    edges = (tracker.dependency_edge(row) for row in rows)
    return tuple(edge[0] for edge in edges if edge is not None and edge[1] == _BLOCKS)


def titles_to_ids(records: Sequence[Mapping[str, object]]) -> dict[str, tuple[str, ...]]:
    """Every record id keyed by its title, over the **whole** tracker.

    Closed records included: a sibling that has since closed is still what an open body's
    title names, and 2 of the 8 live declarations name exactly that. One title is held by
    two records on this tree, so a title maps to ids rather than to an id.
    """
    held: dict[str, list[str]] = {}
    for record in records:
        title = str(record.get("title") or "")
        if title:
            held.setdefault(title, []).append(str(record.get("id") or ""))
    return {title: tuple(ids) for title, ids in held.items()}


def candidates(
    declared: str, ids: frozenset[str], titles: Mapping[str, tuple[str, ...]]
) -> tuple[str, ...]:
    """The record ids *declared* names, by id first and by title second.

    Id first because that is what the decomposer writes; a body that names a title is
    resolved rather than reported, and one the tracker holds under neither yields nothing.
    """
    if declared in ids:
        return (declared,)
    return titles.get(declared, ())


def reconcile(records: Sequence[Mapping[str, object]]) -> Reconciliation:
    """Hold every open record's declared dependencies against its `blocks` edges."""
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
    """Every reason to refuse: an empty probe first, then each disagreement."""
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
    """Entry point: report every declared dependency the graph does not enforce."""
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
        # A disagreement and an empty probe are both refusals and different findings: the
        # first has a repair to name, the second is the check reporting on itself.
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

"""The dependency edges the owned ledger is short of, as the drafts that close the gap.

`tracker adopt` reconciled the records a hand-run ``br`` created and stopped at the edges
between them (basicly-vkh0.32). A hand-run ``br dep add`` on a record both stores already
hold never reaches `basicly.br._mirror_write`, and br refuses the same edge a second time,
so no seam-routed write can put it in the ledger.

The boundary is *what the ledger is short of* against :mod:`basicly.br`, which spawns the
reads and appends what comes back. The kit modules arrive as parameters, as in
:mod:`basicly.mirror`: nothing here loads a kit, reads a file or runs a process.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Set
from dataclasses import dataclass
from typing import Any

# One edge as ``(target, type)``; the record it sits on is the mapping key below.
Edge = tuple[str, str]


@dataclass(frozen=True)
class EdgeShortfall:
    """The edges one run of the repair may append, and the ones it may not.

    Attributes:
        drafts: The events to append, in record order.
        adopted: What each draft records, as ``(record, target, type)``.
        unexported: Records whose missing edge the committed export does not carry either,
            so re-exporting is the repair. Reported and written nowhere.
    """

    drafts: tuple[Any, ...] = ()
    adopted: tuple[tuple[str, str, str], ...] = ()
    unexported: tuple[str, ...] = ()


def bodyless(kit_module: Any, ledger_events: Iterable[Any]) -> set[str]:
    """Ledger records with no ``created`` event, so it holds no body for them.

    Both of the adoption's other sets miss them: in its record set, so subtracted out
    of ``undeclared``; no ``created`` event, so absent from ``held`` (basicly-vkh0.41).
    """
    events = kit_module.events
    seen: set[str] = set()
    bodied: set[str] = set()
    for event in ledger_events:
        seen.add(str(event.record))
        if event.kind == events.KIND_CREATED:
            bodied.add(str(event.record))
    return seen - bodied


def shortfall(
    kit_module: Any,
    boundary: Any,
    ledger_events: Iterable[Any],
    reference: Mapping[str, Set[Edge]],
    export: Mapping[str, Set[Edge]],
) -> EdgeShortfall:
    """The edges *reference* holds that the ledger does not, for the records in scope.

    Scope is the flip boundary's, read off *boundary*: a record the export imported at the
    flip is excused rather than repaired, and one the record-level import already adopts
    would get the same edge twice under two payloads. A record the ledger has no event for
    is that repair's.

    Detected against the reference and written from the committed *export*, the split
    `br.adopt_hand_writes` already makes for records — an edge copied from the side the
    differential compares against makes that comparison agree by construction, while one
    copied from the export leaves a stale export visible as a real disagreement.
    """
    migrate = kit_module.migrate
    collected = list(ledger_events)
    views = kit_module.views_from_events(collected)
    ledger = {
        record: {(edge.target, edge.type) for edge in view.dependencies}
        for record, view in views.items()
    }
    scope = set(views) - boundary.imported_records(collected) - boundary.adopted_records(collected)
    drafts: list[Any] = []
    adopted: list[tuple[str, str, str]] = []
    unexported: list[str] = []
    for record in sorted(scope.intersection(reference)):
        for target, edge_type in sorted(reference[record] - ledger.get(record, frozenset())):
            if (target, edge_type) not in export.get(record, frozenset()):
                unexported.append(record)
                continue
            payload = {
                migrate.PROVENANCE_KEY: migrate.EXTRACTED,
                migrate.SOURCE_KEY: boundary.ADOPTION_SOURCE,
                migrate.EDGE_FROM: record,
                migrate.EDGE_TO: target,
                migrate.EDGE_TYPE: edge_type,
            }
            drafts.append(kit_module.events.Draft(record, migrate.KIND_EDGE, payload))
            adopted.append((record, target, edge_type))
    return EdgeShortfall(tuple(drafts), tuple(adopted), tuple(sorted(set(unexported))))

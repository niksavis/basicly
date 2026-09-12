from __future__ import annotations

import collections
from pathlib import Path
from typing import Any

from basicly import tracker

REPO_ROOT = Path(__file__).resolve().parent.parent
PARENT = "edge-1"
CHILD = "edge-1.1"
PARENT_CHILD = "parent-child"


def _kit() -> Any:
    return tracker.kit(REPO_ROOT)


def _one_relation_stated_twice(ledger: Path) -> Any:

    kit = _kit()
    events, migrate = kit.events, kit.migrate
    relation = {
        migrate.EDGE_FROM: CHILD,
        migrate.EDGE_TO: PARENT,
        migrate.EDGE_TYPE: PARENT_CHILD,
    }
    events.append(
        ledger,
        [
            events.Draft(PARENT, "created", {"title": "the epic"}),
            events.Draft(CHILD, "created", {"title": "the child"}),
            events.Draft(CHILD, migrate.KIND_EDGE, dict(relation)),
            events.Draft(
                CHILD,
                migrate.KIND_EDGE,
                {
                    **relation,
                    migrate.ASSERTED_AT_KEY: "2026-08-16T15:27:37.836780869Z",
                    migrate.ASSERTED_BY_KEY: "an-importer",
                },
            ),
        ],
    )
    return kit


def test_two_events_stating_one_relation_fold_to_one_edge(tmp_path: Path) -> None:

    kit = _one_relation_stated_twice(tmp_path)
    collected = kit.read_ledger(tmp_path)

    stated = [event for event in collected if event.kind == kit.migrate.KIND_EDGE]
    views = kit.views_from_events(collected)

    assert len(stated) == 2
    assert views[CHILD].dependencies == (kit.Edge(target=PARENT, type=PARENT_CHILD),)


def test_the_surviving_edge_carries_the_stronger_provenance_label(tmp_path: Path) -> None:

    kit = _kit()
    events, provenance = kit.events, kit.provenance
    key = provenance.EdgeKey(source=CHILD, edge_type=PARENT_CHILD, target=PARENT)
    events.append(
        tmp_path,
        [
            provenance.edge_draft(key, provenance.INFERRED, detail="scope globs overlap"),
            provenance.edge_draft(key, provenance.EXTRACTED, detail="the owner said so"),
        ],
    )
    collected = kit.read_ledger(tmp_path)

    edge_fold = provenance.fold_edges(collected)

    assert list(edge_fold.edges) == [key]
    assert edge_fold.edges[key].label == provenance.EXTRACTED
    assert [item.label for item in edge_fold.edges[key].history] == [
        provenance.INFERRED,
        provenance.EXTRACTED,
    ]
    assert len(kit.views_from_events(collected)[CHILD].dependencies) == 1


def test_the_committed_ledger_folds_every_stated_relation_to_one_row() -> None:

    kit = _kit()
    labels = kit.provenance.labels
    collected = kit.read_ledger(tracker.ledger_dir(REPO_ROOT))
    edge_kinds = (kit.migrate.KIND_EDGE, kit.events.KIND_EDGE_RETRACTED)

    stated: collections.Counter[tuple[str, object, object]] = collections.Counter()
    for event in collected:
        if event.kind in edge_kinds:
            payload = event.payload
            keys = labels.DIALECT_KEYS[kit.provenance.edge_dialect(payload)]
            stated[(event.record, payload.get(keys[1]), payload.get(keys[0]))] += 1
    twice = [relation for relation, count in stated.items() if count > 1]

    assert twice, "no relation in the committed ledger is stated twice; the subject is gone"

    inflated = []
    for record, view in kit.views_from_events(collected).items():
        rows = [edge for edge in view.dependencies if edge.type == PARENT_CHILD]
        if len(rows) != len({edge.target for edge in rows}):
            inflated.append((record, len(rows), len({edge.target for edge in rows})))

    assert inflated == []

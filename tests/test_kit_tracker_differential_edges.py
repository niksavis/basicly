"""One relation is one row, however many events stated it (basicly-vkh0.52).

`basicly tracker show basicly-vkh0` reported 65 parent-child rows for 60 distinct ids, the
five repeats consecutive, so every count taken off that surface overstated the child set by
five. The cause is in the log and is still there: **19 relations in the committed ledger are
stated by two edge events each**, 9 of them parent-child. `migrate._plan_record` maps an
imported record's `created_at`/`created_by` onto `asserted_at`/`asserted_by`, so an import
that carried them and one that did not mint two content-derived ids for one relation, and
`events.append`'s replay skip — which is by id — cannot see that they say the same thing.

The fold has answered one edge since `7a2a10ab` (2026-08-20) keyed the per-record edge store
by edge identity for the retraction feature. That was **incidental and nothing bound it**:
the `dict[str, list[Edge]]` it replaced, run over today's log, puts 71 parent-child rows on
`basicly-vkh0` against 66 distinct ids. These tests bind it at the fold and over the
committed ledger, which is the population that would show it again.

The kit is loaded through :func:`basicly.tracker.kit` rather than by path, and the ledger's
location through :func:`basicly.tracker.ledger_dir`: inside a worktree the ledger is a
redirect to the base checkout's, so a test resolving `.basicly/ledger` itself would read an
empty directory and assert nothing.
"""

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
    """The tree's own kit, one load, so there is one ``Event`` class in play."""
    return tracker.kit(REPO_ROOT)


def _one_relation_stated_twice(ledger: Path) -> Any:
    """A ledger where two edge events state ``CHILD -[parent-child]-> PARENT``.

    The pair differs exactly as the committed ledger's nine do — the second carries the
    importer's `asserted_at`/`asserted_by`, the first does not — which is what gives them
    two content-derived ids for one relation.
    """
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
    """The log keeps both events; the derived view holds one edge.

    Both halves matter: the append-only log is not the defect — it holds every event it was
    given — and a reader counting rows must still see the relation once.
    """
    kit = _one_relation_stated_twice(tmp_path)
    collected = kit.read_ledger(tmp_path)

    stated = [event for event in collected if event.kind == kit.migrate.KIND_EDGE]
    views = kit.views_from_events(collected)

    assert len(stated) == 2
    assert views[CHILD].dependencies == (kit.Edge(target=PARENT, type=PARENT_CHILD),)


def test_the_surviving_edge_carries_the_stronger_provenance_label(tmp_path: Path) -> None:
    """AC2, and the cross-check that the two folds read one edge set.

    `provenance.fold_edges` is the labelled fold and `views_from_events` the one
    `tracker show` renders. A relation asserted twice under different labels is one edge in
    both, and in the labelled one it keeps the stronger claim with the weaker in its
    history rather than as a second row.
    """
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
    """AC3 over this repo's own log, which is the population that produced the defect.

    The duplicate-stated relations are asserted first: without them a green run would mean
    "the subject is gone" and this test would pass on a log that could not fail it.
    """
    kit = _kit()
    labels = kit.provenance.labels
    collected = kit.read_ledger(tracker.ledger_dir(REPO_ROOT))
    edge_kinds = (kit.migrate.KIND_EDGE, kit.events.KIND_EDGE_RETRACTED)

    stated: collections.Counter[tuple[str, object, object]] = collections.Counter()
    for event in collected:
        if event.kind in edge_kinds:
            # The dialect table the folds themselves read, never a second copy of it: two
            # readers with two copies is how one of them came to see none of these
            # events (basicly-oii83r).
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

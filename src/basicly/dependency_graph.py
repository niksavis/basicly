from __future__ import annotations

from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from basicly import owned_store


def _views(repo_root: Path) -> tuple[Any, Mapping[str, Any]]:
    kit_module = owned_store.kit(repo_root)
    found = kit_module.read_ledger(owned_store.ledger_dir(repo_root))
    return kit_module, kit_module.views_from_events(found)


def owned_blocked(repo_root: Path) -> tuple[str, ...]:

    kit_module, views = _views(repo_root)
    vocabulary = kit_module.DEFAULT_VOCABULARY
    blocked = []
    for record in sorted(views):
        view = views[record]
        if view.tombstoned or not kit_module.is_dispatchable(view.status, vocabulary):
            continue
        for edge in view.dependencies:
            if edge.type not in vocabulary.blocking_types:
                continue
            blocker = views.get(edge.target)
            if blocker is None or blocker.status not in vocabulary.closed_statuses:
                blocked.append(record)
                break
    return tuple(blocked)


def blocked(repo_root: Path) -> tuple[str, ...]:
    return owned_blocked(repo_root)


def _blocking_edges(kit_module: Any, views: Mapping[str, Any]) -> dict[str, frozenset[str]]:

    types = kit_module.DEFAULT_VOCABULARY.blocking_types
    return {
        record: frozenset(
            edge.target for edge in view.dependencies if edge.type in types and edge.target in views
        )
        for record, view in views.items()
    }


def strong_components(edges: Mapping[str, frozenset[str]]) -> list[tuple[str, ...]]:

    index: dict[str, int] = {}
    low: dict[str, int] = {}
    on_stack: set[str] = set()
    stack: list[str] = []
    found: list[tuple[str, ...]] = []
    counter = 0
    for root in sorted(edges):
        if root in index:
            continue
        index[root] = low[root] = counter
        counter += 1
        stack.append(root)
        on_stack.add(root)
        work: list[tuple[str, Iterator[str]]] = [(root, iter(sorted(edges[root])))]
        while work:
            node, successors = work[-1]
            descended = False
            for successor in successors:
                if successor not in index:
                    index[successor] = low[successor] = counter
                    counter += 1
                    stack.append(successor)
                    on_stack.add(successor)
                    work.append((successor, iter(sorted(edges.get(successor, frozenset())))))
                    descended = True
                    break
                if successor in on_stack:
                    low[node] = min(low[node], index[successor])
            if descended:
                continue
            work.pop()
            if work:
                parent = work[-1][0]
                low[parent] = min(low[parent], low[node])
            if low[node] != index[node]:
                continue
            component = []
            while True:
                member = stack.pop()
                on_stack.discard(member)
                component.append(member)
                if member == node:
                    break
            if len(component) > 1 or node in edges.get(node, frozenset()):
                found.append(tuple(sorted(component)))
    return sorted(found)


def owned_cycles(repo_root: Path) -> tuple[tuple[str, ...], ...]:

    kit_module, views = _views(repo_root)
    return tuple(strong_components(_blocking_edges(kit_module, views)))


def blocking_cycles(repo_root: Path) -> tuple[tuple[str, ...], ...]:
    return owned_cycles(repo_root)

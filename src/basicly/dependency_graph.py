"""Which store answers *what does the blocking-dependency graph say* (basicly-wpc8).

Two questions, both derived from the same edges: which records are held back by an
unsatisfied blocker, and which records a blocking cycle runs through. The cutover rung
decides where the edges come from — the owned ledger's fold, or a ``br blocked`` and
``br dep cycles`` spawn.

The boundary is *the graph* against :mod:`basicly.gate_source`, which answers one
record's gate rows, and against :mod:`basicly.br`, which answers one record and the
ranked ready set. Each store answers in the engine's own shape, so the callers
(`loop_state.blocked_ids`, `decompose._assert_no_new_cycles`) have one parser.
"""

from __future__ import annotations

import json
from collections.abc import Iterator, Mapping
from pathlib import Path
from typing import Any

from basicly import br, owned_store


def _views(repo_root: Path) -> tuple[Any, Mapping[str, Any]]:
    """The kit and every record it holds as a view, for one whole-population read."""
    kit_module = owned_store.kit(repo_root)
    found = kit_module.read_ledger(owned_store.ledger_dir(repo_root))
    return kit_module, kit_module.views_from_events(found)


def owned_blocked(repo_root: Path) -> tuple[str, ...]:
    """The ids the owned ledger says are waiting on a dependency, in id order.

    Clause 2 of `differential.is_ready`, on its own: a record is blocked when a blocking
    edge points at something the population does not hold, or holds at a status that is
    not terminal. An edge into an unknown record counts as unsatisfied, because an
    unknown blocker is unknown rather than absent.

    A record that cannot be dispatched at all is not blocked — it is closed, tombstoned
    or deferred — so the status clause runs first. `is_ready`'s third clause is
    deliberately not here: a decomposed parent is not the work, but it is not waiting on
    a dependency either, and ``br blocked`` does not report it as one.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load. A hard failure,
            because an empty answer reads as nothing being held back.
    """
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


def external_blocked(repo_root: Path) -> tuple[str, ...]:
    """The ids ``br blocked --json`` reports.

    Raises:
        RuntimeError: br could not be run, or its reply was not usable JSON.
    """
    proc = br.run_br(repo_root, ["blocked", "--json"])
    try:
        issues = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br blocked returned no usable JSON: {exc}") from exc
    return tuple(
        str(issue["id"]) for issue in issues or () if isinstance(issue, dict) and "id" in issue
    )


def blocked(repo_root: Path) -> tuple[str, ...]:
    """The ids waiting on a dependency, from whichever store the declared rung names."""
    if owned_store.tracker_mode(repo_root) == owned_store.MODE_OWNED:
        return owned_blocked(repo_root)
    return external_blocked(repo_root)


def _blocking_edges(kit_module: Any, views: Mapping[str, Any]) -> dict[str, frozenset[str]]:
    """Each record's blocking successors, restricted to records the population holds.

    An edge into a record the ledger does not hold cannot close a cycle, and keeping it
    would put an id in a component that has no events at all.
    """
    types = kit_module.DEFAULT_VOCABULARY.blocking_types
    return {
        record: frozenset(
            edge.target for edge in view.dependencies if edge.type in types and edge.target in views
        )
        for record, view in views.items()
    }


def strong_components(edges: Mapping[str, frozenset[str]]) -> list[tuple[str, ...]]:
    """The cyclic strongly-connected components of *edges*, each sorted, in sorted order.

    **Components rather than elementary cycles**, and that is a decision rather than an
    approximation: enumerating every distinct cycle is exponential in the graph, while the
    one caller asks whether a cycle touches the ids it just created — a question a
    component answers exactly, because every member of a cyclic component lies on a cycle
    through every other member.

    Tarjan's algorithm, iterative so a deep chain cannot exhaust the interpreter stack,
    and over sorted neighbours so one graph always yields one answer. That determinism is
    the point of writing it here: the external tracker's own cycle report depends on which
    node it started from, so the same graph can answer twice.
    """
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
            # A one-member component is cyclic only through a self-edge, which the
            # low-link test cannot tell from an ordinary leaf.
            if len(component) > 1 or node in edges.get(node, frozenset()):
                found.append(tuple(sorted(component)))
    return sorted(found)


def owned_cycles(repo_root: Path) -> tuple[tuple[str, ...], ...]:
    """The record groups a blocking cycle runs through, out of the owned ledger.

    Raises:
        TrackerDivergenceError: the kit is not installed or will not load.
    """
    kit_module, views = _views(repo_root)
    return tuple(strong_components(_blocking_edges(kit_module, views)))


def external_cycles(repo_root: Path) -> tuple[tuple[str, ...], ...]:
    """The record groups ``br dep cycles --blocking-only --json`` reports.

    br spells a cycle either as a bare list of ids or as an object carrying ``issues``,
    so both are read; a row in neither shape yields no members rather than raising, which
    is the direction the caller's own refusal already handles.

    Raises:
        RuntimeError: br could not be run, or its reply was not usable JSON.
    """
    proc = br.run_br(repo_root, ["dep", "cycles", "--blocking-only", "--json"])
    try:
        report = json.loads(proc.stdout)
    except ValueError as exc:
        raise RuntimeError(f"br dep cycles returned no usable JSON: {exc}") from exc
    cycles = report.get("cycles") if isinstance(report, dict) else None
    found = []
    for cycle in cycles if isinstance(cycles, list) else ():
        members = cycle if isinstance(cycle, list) else cycle.get("issues", [])
        found.append(tuple(sorted(str(member) for member in members or ())))
    return tuple(found)


def blocking_cycles(repo_root: Path) -> tuple[tuple[str, ...], ...]:
    """Every blocking cycle's members, from whichever store the declared rung names."""
    if owned_store.tracker_mode(repo_root) == owned_store.MODE_OWNED:
        return owned_cycles(repo_root)
    return external_cycles(repo_root)

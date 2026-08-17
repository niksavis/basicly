"""The blocking-dependency graph, rung by rung (basicly-wpc8).

Two questions the external tracker used to answer at their own call sites: which records
are held back, and which records a blocking cycle runs through. What has to be shown is a
*comparison* rather than a description, because both stores answering the same way is the
ordinary state and proves nothing — so the flipped tests make the stand-in br hold the
other answer, and fail if any process is spawned at all.

The cycle finder gets its own section. It is the one piece here that is not a store read,
and the external tracker's own report is order-dependent, so determinism is asserted
rather than assumed.
"""

from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from basicly import br, dependency_graph, owned_store
from tests.test_owned_write import no_br, owned_repo

__all__ = ["no_br"]  # re-exported so the fixture resolves in this module


def _proc(stdout: str) -> subprocess.CompletedProcess[str]:
    return subprocess.CompletedProcess(["br"], 0, stdout, "")


def _graph(repo: Path, edges: dict[str, list[tuple[str, str]]], statuses: dict[str, str]) -> None:
    """Seed the ledger with *statuses* and the ``(target, type)`` *edges* on each record."""
    kit = owned_store.kit(repo)
    drafts = [
        kit.events.Draft(record, kit.events.KIND_STATUS, {"status": status})
        for record, status in statuses.items()
    ]
    for record, rows in edges.items():
        for target, edge_type in rows:
            drafts.append(
                kit.events.Draft(
                    record,
                    kit.migrate.KIND_EDGE,
                    {
                        kit.migrate.EDGE_FROM: record,
                        kit.migrate.EDGE_TO: target,
                        kit.migrate.EDGE_TYPE: edge_type,
                    },
                )
            )
    kit.events.append(owned_store.ledger_dir(repo), drafts)


# --- the blocked set ----------------------------------------------------------


@pytest.mark.usefixtures("no_br")
def test_an_open_blocker_holds_its_dependent_and_a_closed_one_does_not(tmp_path: Path) -> None:
    """The rule, with its own control: the same edge onto a closed record blocks nothing."""
    repo = owned_repo(tmp_path)
    _graph(
        repo,
        {"wpc-1.2": [("wpc-1.1", "blocks")], "wpc-1.4": [("wpc-1.3", "blocks")]},
        {
            "wpc-1.1": "open",
            "wpc-1.2": "open",
            "wpc-1.3": "closed",
            "wpc-1.4": "open",
        },
    )

    assert dependency_graph.owned_blocked(repo) == ("wpc-1.2",)


@pytest.mark.usefixtures("no_br")
def test_an_edge_into_a_record_the_ledger_does_not_hold_still_blocks(tmp_path: Path) -> None:
    """An unknown blocker is unknown, never satisfied — `differential.is_ready`'s rule.

    The fail-open direction would hand out work whose real blocker is simply outside the
    population this read folded.
    """
    repo = owned_repo(tmp_path)
    _graph(repo, {"wpc-1.2": [("wpc-9.9", "blocks")]}, {"wpc-1.2": "open"})

    assert dependency_graph.owned_blocked(repo) == ("wpc-1.2",)


@pytest.mark.usefixtures("no_br")
def test_a_parent_child_edge_is_not_a_blocker_and_a_closed_dependent_is_not_blocked(
    tmp_path: Path,
) -> None:
    """Two exclusions in one population, so neither can pass by the other's absence.

    A decomposed parent is not the work but it is not *waiting* on a dependency either,
    and a record that cannot be dispatched at all has nothing to be held back from.
    """
    repo = owned_repo(tmp_path)
    _graph(
        repo,
        {"wpc-1.1": [("wpc-1", "parent-child")], "wpc-1.3": [("wpc-1.1", "blocks")]},
        {"wpc-1": "open", "wpc-1.1": "open", "wpc-1.3": "closed"},
    )

    assert dependency_graph.owned_blocked(repo) == ()


@pytest.mark.usefixtures("no_br")
def test_a_tombstoned_record_is_not_reported_as_blocked(tmp_path: Path) -> None:
    """The absence rule `br.owned_record` states, at the set read.

    A deleted bead keeps its status in the fold, so without this clause it would still be
    reported — and the loop would explain a lane's hold by a bead nobody can open.
    """
    repo = owned_repo(tmp_path)
    _graph(repo, {"wpc-1.2": [("wpc-1.1", "blocks")]}, {"wpc-1.1": "open", "wpc-1.2": "open"})
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [kit.events.Draft("wpc-1.2", kit.events.KIND_TOMBSTONE, {})],
    )

    assert dependency_graph.owned_blocked(repo) == ()


def test_the_external_half_reads_brs_own_payload(tmp_path: Path) -> None:
    """A bare list of records is br's shape; a row carrying no id is skipped, not raised."""
    repo = owned_repo(tmp_path, owned_store.MODE_DUAL)
    payload = json.dumps([{"id": "wpc-1.2"}, {"no": "id"}, "not an object"])

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(br, "run_br", lambda *_a, **_k: _proc(payload))
        assert dependency_graph.blocked(repo) == ("wpc-1.2",)


def test_the_flipped_read_answers_from_the_fold_while_br_holds_the_other_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The comparison that makes the flip visible: the two stores are made to disagree."""
    repo = owned_repo(tmp_path)
    _graph(repo, {"wpc-1.2": [("wpc-1.1", "blocks")]}, {"wpc-1.1": "open", "wpc-1.2": "open"})
    monkeypatch.setattr(
        br, "run_br", lambda *_a, **_k: pytest.fail("the flipped read spawned a process")
    )

    assert dependency_graph.blocked(repo) == ("wpc-1.2",)


# --- the cycle finder ---------------------------------------------------------


def test_a_two_record_cycle_is_reported_and_a_chain_is_not() -> None:
    """The finding beside its control: an acyclic chain over the same nodes reports nothing."""
    cyclic = {"a": frozenset({"b"}), "b": frozenset({"a"})}
    chain = {"a": frozenset({"b"}), "b": frozenset()}

    assert dependency_graph.strong_components(cyclic) == [("a", "b")]
    assert dependency_graph.strong_components(chain) == []


def test_a_self_edge_is_a_cycle_even_though_its_component_holds_one_record() -> None:
    """The low-link test alone cannot tell a self-blocking record from a leaf."""
    assert dependency_graph.strong_components({"a": frozenset({"a"})}) == [("a",)]


def test_two_separate_cycles_are_both_reported_whatever_order_they_are_named_in() -> None:
    """The determinism the external report does not have.

    br's own cycle check starts from whichever node it reached first, so one graph can
    answer twice. Asserted by relabelling the input rather than by re-running it, which is
    what a memoised or accidentally-stable implementation would also pass.
    """
    first = {
        "a": frozenset({"b"}),
        "b": frozenset({"a"}),
        "y": frozenset({"z"}),
        "z": frozenset({"y"}),
        "m": frozenset({"a", "z"}),
    }
    reordered = {name: first[name] for name in ("z", "m", "b", "y", "a")}

    assert dependency_graph.strong_components(first) == [("a", "b"), ("y", "z")]
    assert dependency_graph.strong_components(reordered) == [("a", "b"), ("y", "z")]


def test_a_long_chain_does_not_exhaust_the_interpreter_stack() -> None:
    """Iterative rather than recursive: a 5000-deep graph is inside a real tracker's reach."""
    depth = 5000
    edges = {f"n-{i}": frozenset({f"n-{i + 1}"}) for i in range(depth)}
    edges[f"n-{depth}"] = frozenset()

    assert dependency_graph.strong_components(edges) == []


@pytest.mark.usefixtures("no_br")
def test_the_flipped_cycle_read_finds_a_cycle_the_ledgers_edges_close(tmp_path: Path) -> None:
    """End to end on the owned side, and non-blocking edges are excluded.

    The ``related`` pair is the discriminator: coupling edges do not gate, so a cycle drawn
    only out of them is not a cycle this query may report.
    """
    repo = owned_repo(tmp_path)
    _graph(
        repo,
        {
            "wpc-1.1": [("wpc-1.2", "blocks")],
            "wpc-1.2": [("wpc-1.1", "blocks")],
            "wpc-1.3": [("wpc-1.4", "related")],
            "wpc-1.4": [("wpc-1.3", "related")],
        },
        dict.fromkeys(("wpc-1.1", "wpc-1.2", "wpc-1.3", "wpc-1.4"), "open"),
    )

    assert dependency_graph.blocking_cycles(repo) == (("wpc-1.1", "wpc-1.2"),)


def test_the_external_cycle_read_accepts_both_shapes_br_reports(tmp_path: Path) -> None:
    """A cycle is spelled either as a bare list or as an object carrying ``issues``."""
    repo = owned_repo(tmp_path, owned_store.MODE_DUAL)
    payload = json.dumps({"cycles": [["b", "a"], {"issues": ["d", "c"]}]})

    with pytest.MonkeyPatch.context() as patch:
        patch.setattr(br, "run_br", lambda *_a, **_k: _proc(payload))
        assert dependency_graph.blocking_cycles(repo) == (("a", "b"), ("c", "d"))

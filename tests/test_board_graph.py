"""Parallel or a queue, asserted against the question the owner could not answer.

*"Is the work parallel or sequential"* — and the page carried `DEP EDGES 846` beside
`BLOCKED 56` and settled nothing (basicly-pck9fx). So the cases here are the three answers
worth the space, and the two ways each of them lies:

* **the frontier**, which is wrong if a closed blocker still counts, or if a cycle in a
  producer's edge set makes the depth walk never end;
* **the top blocker**, which is wrong if the edge direction is inverted — and then every
  blocker reads as blocked;
* **the longest chain**, which is wrong if the walk takes the first branch instead of the
  deepest, because then it understates the sequence.

The direction is the load-bearing one. `basicly tracker blocked` reports `basicly-4t9z
blocked by basicly-7bur` for the edge `{from: basicly-4t9z, to: basicly-7bur}`, so `from` is
the blocked record. That was measured, and a test holds it so it cannot be re-guessed.
"""

from __future__ import annotations

from typing import Any

from basicly import board_graph, board_wall


def _reads(units: list[dict], edges: list[dict] | None) -> board_wall.Readings:
    """Readings carrying just the two sections the queue reads."""
    reads = board_wall.Readings()
    live = board_wall.BY_KEY[board_wall.RENDERABLE]
    reads["units"] = board_wall.Reading("units", live, "", units)
    if edges is None:
        reads["graph"] = board_wall.Reading("graph", board_wall.BY_KEY[board_wall.ABSENT], "absent")
    else:
        reads["graph"] = board_wall.Reading("graph", live, "", {"edges": edges})
    return reads


def _unit(ident: str) -> dict[str, Any]:
    """One active record."""
    return {"id": ident, "phase": "intake", "status": "open", "ready": True}


def _blocks(blocked: str, blocker: str) -> dict[str, str]:
    """The edge shape the producer writes: *blocked* waits on *blocker*."""
    return {"from": blocked, "to": blocker, "kind": "blocks"}


def test_the_frontier_separates_what_can_start_now_from_what_waits() -> None:
    """The owner's question in one line: 261 with nothing behind them against 32 that wait.

    Here in miniature - one free, one behind one thing, one behind a chain of two - so the
    three bands are each driven and none is inferred from the others.
    """
    units = [_unit(f"r{n}") for n in range(4)]
    edges = [_blocks("r1", "r0"), _blocks("r2", "r1"), _blocks("r3", "r2")]
    q = board_graph.queue(_reads(units, edges))
    assert [(b.label, b.count) for b in q.bands] == [
        ("needs nothing", 1),
        ("waits on one", 1),
        ("waits on a chain", 2),
    ]
    assert q.bands[0].share is not None and q.bands[0].share.label == "25%"


def test_from_is_the_blocked_record_and_to_is_the_blocker() -> None:
    """Measured against `tracker blocked`, and held here so it cannot be re-guessed.

    Inverting it reports every blocker as blocked, which reads as a plausible board and is
    the exact opposite of the truth.
    """
    q = board_graph.queue(
        _reads(
            [_unit("basicly-4t9z"), _unit("basicly-7bur")],
            [_blocks("basicly-4t9z", "basicly-7bur")],
        )
    )
    assert [b.ident for b in q.blockers] == ["basicly-7bur"], "the direction is inverted"
    assert q.blockers[0].blocking == 1
    assert q.chain[0] == "basicly-4t9z", "the chain starts at what waits, not at what holds"


def test_a_blocker_the_document_no_longer_lists_holds_nothing() -> None:
    """A closed blocker is a debt already paid; counting it narrows the frontier falsely."""
    units = [_unit("r1")]
    q = board_graph.queue(_reads(units, [_blocks("r1", "r-closed")]))
    assert q.blockers == ()
    assert "nothing waits on anything" in q.note
    # The control: with the blocker present, the same edge does block.
    held = board_graph.queue(_reads([_unit("r1"), _unit("r-closed")], [_blocks("r1", "r-closed")]))
    assert held.blockers and held.blockers[0].ident == "r-closed"


def test_the_record_that_unblocks_the_most_is_named_with_its_count() -> None:
    """The second term the scheduler ranks on, which the page never showed."""
    units = [_unit(f"r{n}") for n in range(5)]
    edges = [_blocks("r1", "r0"), _blocks("r2", "r0"), _blocks("r3", "r0"), _blocks("r4", "r3")]
    q = board_graph.queue(_reads(units, edges))
    assert (q.blockers[0].ident, q.blockers[0].blocking) == ("r0", 3)
    assert [b.ident for b in q.blockers] == ["r0", "r3"]


def test_the_blockers_are_bounded_and_the_rest_is_reported() -> None:
    """An unbounded row on a wall is the appended panel again."""
    holders = board_graph.BLOCKER_SLOTS + 2
    units = [_unit(f"h{n}") for n in range(holders)] + [_unit(f"w{n}") for n in range(holders)]
    edges = [_blocks(f"w{n}", f"h{n}") for n in range(holders)]
    q = board_graph.queue(_reads(units, edges))
    assert len(q.blockers) == board_graph.BLOCKER_SLOTS
    assert "2" in q.dropped and "blockers" in q.dropped


def test_the_longest_chain_takes_the_deepest_branch_not_the_first() -> None:
    """A shorter branch would understate the sequence, which is the figure's whole point."""
    units = [_unit(n) for n in ("top", "a", "aa", "aaa", "b")]
    # `top` waits on `a` and on `b`; `a` runs three deep and `b` stops at one.
    edges = [
        _blocks("top", "b"),
        _blocks("top", "a"),
        _blocks("a", "aa"),
        _blocks("aa", "aaa"),
    ]
    q = board_graph.queue(_reads(units, edges))
    assert q.chain == ("top", "a", "aa", "aaa")
    assert "the chain runs 3 deep" in q.note


def test_the_chain_is_bounded_so_a_long_one_cannot_run_off_the_page() -> None:
    """A wall row is a promise about a rendered width."""
    depth = board_graph.CHAIN_SLOTS + 3
    units = [_unit(f"r{n}") for n in range(depth)]
    edges = [_blocks(f"r{n}", f"r{n + 1}") for n in range(depth - 1)]
    q = board_graph.queue(_reads(units, edges))
    assert len(q.chain) == board_graph.CHAIN_SLOTS
    # The note still states the true depth, so the bound does not hide it.
    assert f"the chain runs {depth - 1} deep" in q.note


def test_a_cycle_in_the_producers_edges_terminates() -> None:
    """The edge set is a producer's and this consumer may not assume it is acyclic.

    Without the guard the depth walk recurses forever and the page never renders at all.
    """
    units = [_unit("x"), _unit("y"), _unit("z")]
    edges = [_blocks("x", "y"), _blocks("y", "x"), _blocks("z", "x")]
    q = board_graph.queue(_reads(units, edges))
    assert q.state.key == board_wall.RENDERABLE
    assert sum(band.count for band in q.bands) == len(units)
    assert len(q.chain) <= len(units)


def test_an_absent_graph_says_so_and_never_reads_as_an_unblocked_backlog() -> None:
    """A producer that emits no edges has said nothing about blocking, not that there is none."""
    q = board_graph.queue(_reads([_unit("r0")], None))
    assert q.state.key == board_wall.ABSENT
    assert "graph" in q.note
    assert q.bands == () and q.blockers == () and q.chain == ()


def test_an_edge_set_with_no_blocking_kind_settles_the_question_rather_than_dropping_it() -> None:
    """`parent-child` is the feature tree and binds nothing; the answer is still an answer."""
    units = [_unit("r0"), _unit("r1")]
    q = board_graph.queue(_reads(units, [{"from": "r1", "to": "r0", "kind": "parent-child"}]))
    assert q.state.key == board_wall.RENDERABLE
    assert q.note == "nothing waits on anything, over 2 records"


def test_a_malformed_edge_is_dropped_and_costs_no_others() -> None:
    """A foreign producer's first honest attempt, which must not blank the region."""
    units = [_unit("r0"), _unit("r1")]
    edges: list[Any] = ["not an edge", {"kind": "blocks"}, {}, _blocks("r1", "r0")]
    q = board_graph.queue(_reads(units, edges))
    assert [b.ident for b in q.blockers] == ["r0"]

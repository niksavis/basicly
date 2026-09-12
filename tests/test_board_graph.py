from __future__ import annotations

from typing import Any

from basicly import board_graph, board_wall


def _reads(units: list[dict], edges: list[dict] | None) -> board_wall.Readings:
    reads = board_wall.Readings()
    live = board_wall.BY_KEY[board_wall.RENDERABLE]
    reads["units"] = board_wall.Reading("units", live, "", units)
    if edges is None:
        reads["graph"] = board_wall.Reading("graph", board_wall.BY_KEY[board_wall.ABSENT], "absent")
    else:
        reads["graph"] = board_wall.Reading("graph", live, "", {"edges": edges})
    return reads


def _unit(ident: str) -> dict[str, Any]:
    return {"id": ident, "phase": "intake", "status": "open", "ready": True}


def _blocks(blocked: str, blocker: str) -> dict[str, str]:
    return {"from": blocked, "to": blocker, "kind": "blocks"}


def test_the_frontier_separates_what_can_start_now_from_what_waits() -> None:

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
    units = [_unit("r1")]
    q = board_graph.queue(_reads(units, [_blocks("r1", "r-closed")]))
    assert q.blockers == ()
    assert "nothing waits on anything" in q.note
    held = board_graph.queue(_reads([_unit("r1"), _unit("r-closed")], [_blocks("r1", "r-closed")]))
    assert held.blockers and held.blockers[0].ident == "r-closed"


def test_the_record_that_unblocks_the_most_is_named_with_its_count() -> None:
    units = [_unit(f"r{n}") for n in range(5)]
    edges = [_blocks("r1", "r0"), _blocks("r2", "r0"), _blocks("r3", "r0"), _blocks("r4", "r3")]
    q = board_graph.queue(_reads(units, edges))
    assert (q.blockers[0].ident, q.blockers[0].blocking) == ("r0", 3)
    assert [b.ident for b in q.blockers] == ["r0", "r3"]


def test_the_blockers_are_bounded_and_the_rest_is_reported() -> None:
    holders = board_graph.BLOCKER_SLOTS + 2
    units = [_unit(f"h{n}") for n in range(holders)] + [_unit(f"w{n}") for n in range(holders)]
    edges = [_blocks(f"w{n}", f"h{n}") for n in range(holders)]
    q = board_graph.queue(_reads(units, edges))
    assert len(q.blockers) == board_graph.BLOCKER_SLOTS
    assert "2" in q.dropped and "blockers" in q.dropped


def test_the_longest_chain_takes_the_deepest_branch_not_the_first() -> None:
    units = [_unit(n) for n in ("top", "a", "aa", "aaa", "b")]
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
    depth = board_graph.CHAIN_SLOTS + 3
    units = [_unit(f"r{n}") for n in range(depth)]
    edges = [_blocks(f"r{n}", f"r{n + 1}") for n in range(depth - 1)]
    q = board_graph.queue(_reads(units, edges))
    assert len(q.chain) == board_graph.CHAIN_SLOTS
    assert f"the chain runs {depth - 1} deep" in q.note


def test_a_cycle_in_the_producers_edges_terminates() -> None:

    units = [_unit("x"), _unit("y"), _unit("z")]
    edges = [_blocks("x", "y"), _blocks("y", "x"), _blocks("z", "x")]
    q = board_graph.queue(_reads(units, edges))
    assert q.state.key == board_wall.RENDERABLE
    assert sum(band.count for band in q.bands) == len(units)
    assert len(q.chain) <= len(units)


def test_an_absent_graph_says_so_and_never_reads_as_an_unblocked_backlog() -> None:
    q = board_graph.queue(_reads([_unit("r0")], None))
    assert q.state.key == board_wall.ABSENT
    assert "graph" in q.note
    assert q.bands == () and q.blockers == () and q.chain == ()


def test_an_edge_set_with_no_blocking_kind_settles_the_question_rather_than_dropping_it() -> None:
    units = [_unit("r0"), _unit("r1")]
    q = board_graph.queue(_reads(units, [{"from": "r1", "to": "r0", "kind": "parent-child"}]))
    assert q.state.key == board_wall.RENDERABLE
    assert q.note == "nothing waits on anything, over 2 records"


def test_a_malformed_edge_is_dropped_and_costs_no_others() -> None:
    units = [_unit("r0"), _unit("r1")]
    edges: list[Any] = ["not an edge", {"kind": "blocks"}, {}, _blocks("r1", "r0")]
    q = board_graph.queue(_reads(units, edges))
    assert [b.ident for b in q.blockers] == ["r0"]

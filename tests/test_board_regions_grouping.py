"""The next-up region's feature grouping, asserted against the folds that get it wrong.

Split from `test_board_regions.py` under the `test_<module>_<aspect>.py` convention, because
that module is at its size ratchet and a grouping test large enough to discriminate does not
fit in what is left of it.

The operator's report was that a wall row named an id, a priority and a title, and nothing
that said which feature it implemented. Several folds satisfy "a heading appeared" and are
still wrong, so each test below names the wrong fold it refutes.
"""

from __future__ import annotations

from basicly import board_regions, board_wall
from tests.test_board_regions import _absent, _reads


def _units() -> list[dict[str, object]]:
    """Twelve ready tasks two levels under an epic, plus one ready orphan and the ancestors.

    Twelve rather than a handful because `READY_SLOTS` is eight and a count assertion cannot
    discriminate unless the group outgrows the slice.
    """
    units: list[dict[str, object]] = [
        {"id": f"t-{n:02d}", "ready": True, "priority": "P1", "title": "t"} for n in range(12)
    ]
    units.append({"id": "o-1", "ready": True, "priority": "P0", "title": "o"})
    units.append({"id": "feat", "title": "the feature"})
    units.append({"id": "epic", "title": "the epic"})
    return units


def _edges() -> list[dict[str, str]]:
    """The two-level chain: every task hangs off `feat`, and `feat` hangs off `epic`."""
    edges = [{"from": f"t-{n:02d}", "to": "feat", "kind": "parent-child"} for n in range(12)]
    edges.append({"from": "feat", "to": "epic", "kind": "parent-child"})
    return edges


def test_a_row_is_headed_by_its_root_feature_and_not_by_its_immediate_parent() -> None:
    """The heading a reader recognises is the epic, and `feat` is the refutation.

    A fold reading `parents[ident]` once would head these rows *the feature*, which is a name
    the operator's complaint would survive intact. The assertion is that `the feature` appears
    nowhere among the headings even though it is a real, titled, intermediate ancestor.
    """
    listing = board_regions.next_up(
        _reads("wall-v1.json", units=_units(), graph={"edges": _edges()})
    )
    assert [group.name for group in listing.groups] == ["the epic", board_wall.UNATTACHED]


def test_a_heading_counts_the_whole_ready_set_and_not_the_slice_the_region_draws() -> None:
    """The demonstration's own criterion, and the number a slice-derived count would print.

    Twelve units are ready under `the epic` and `READY_SLOTS` draws eight rows in total, so a
    count folded from the drawn rows reads 7 where the document holds 12. The pair is asserted
    together - the count *and* the row total - because either alone passes the wrong fold.
    """
    listing = board_regions.next_up(
        _reads("wall-v1.json", units=_units(), graph={"edges": _edges()})
    )
    assert [(group.name, group.count) for group in listing.groups] == [
        ("the epic", "12"),
        (board_wall.UNATTACHED, "1"),
    ]
    drawn = sum(len(group.rows) for group in listing.groups)
    assert drawn + len(listing.groups) == board_regions.READY_SLOTS, "a heading costs a slot"
    assert len(listing.groups[0].rows) < 12, "the slice must be smaller than the count"


def test_the_unattached_group_sorts_last_however_its_rows_rank() -> None:
    """`o-1` is the only P0 in the set, so every rank-following fold puts it first.

    That is the discriminator: the group order follows each group's best row, and this one
    rule is deliberately exempt from it, because a row attached to no feature is a filing gap
    rather than the most urgent thing on the wall.
    """
    listing = board_regions.next_up(
        _reads("wall-v1.json", units=_units(), graph={"edges": _edges()})
    )
    assert listing.rows[0].ident == "o-1", "the ranker no longer puts the orphan first"
    assert listing.groups[-1].name == board_wall.UNATTACHED


def test_a_document_without_a_graph_still_draws_every_row_under_one_heading() -> None:
    """The graph section is optional, and withholding the region would be the worse answer.

    A producer that emits no `graph` leaves the fold with no parent edges at all. Every ready
    row must then fold to the single unattached heading, counted in full, with the region
    still rendering rather than reporting itself unreadable.
    """
    bare = board_regions.next_up(_absent("graph", _reads("wall-v1.json", units=_units())))
    assert bare.state.key == board_wall.RENDERABLE
    assert [(group.name, group.count) for group in bare.groups] == [(board_wall.UNATTACHED, "13")]

from __future__ import annotations

from basicly import board_regions, board_wall
from tests.test_board_regions import _absent, _reads


def _units() -> list[dict[str, object]]:

    units: list[dict[str, object]] = [
        {"id": f"t-{n:02d}", "ready": True, "priority": "P1", "title": "t"} for n in range(12)
    ]
    units.append({"id": "o-1", "ready": True, "priority": "P0", "title": "o"})
    units.append({"id": "feat", "title": "the feature"})
    units.append({"id": "epic", "title": "the epic"})
    return units


def _edges() -> list[dict[str, str]]:
    edges = [{"from": f"t-{n:02d}", "to": "feat", "kind": "parent-child"} for n in range(12)]
    edges.append({"from": "feat", "to": "epic", "kind": "parent-child"})
    return edges


def test_a_row_is_headed_by_its_root_feature_and_not_by_its_immediate_parent() -> None:

    listing = board_regions.next_up(
        _reads("wall-v1.json", units=_units(), graph={"edges": _edges()})
    )
    assert [group.name for group in listing.groups] == ["the epic", board_wall.UNATTACHED]


def test_a_heading_counts_the_whole_ready_set_and_not_the_slice_the_region_draws() -> None:

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

    listing = board_regions.next_up(
        _reads("wall-v1.json", units=_units(), graph={"edges": _edges()})
    )
    assert listing.rows[0].ident == "o-1", "the ranker no longer puts the orphan first"
    assert listing.groups[-1].name == board_wall.UNATTACHED


def test_a_document_without_a_graph_still_draws_every_row_under_one_heading() -> None:

    bare = board_regions.next_up(_absent("graph", _reads("wall-v1.json", units=_units())))
    assert bare.state.key == board_wall.RENDERABLE
    assert [(group.name, group.count) for group in bare.groups] == [(board_wall.UNATTACHED, "13")]

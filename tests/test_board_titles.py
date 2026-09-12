from __future__ import annotations

import html
import re
from typing import Any

import pytest

from basicly import board_graph, board_regions, board_schema, board_wall
from tests.test_board_operator_questions import REPO_ROOT
from tests.test_board_operator_questions import page as _wall
from tests.test_board_wall import document

SHAPE_ELEMENTS = board_graph.SHAPE_ELEMENTS

REGION = re.compile(r'<section class="region ([\w-]+)[^"]*"[^>]*>(.*?)</section>', re.DOTALL)


def _without_shapes(chunk: str) -> str:
    for name in SHAPE_ELEMENTS:
        chunk = re.sub(rf'<div class="{name}">.*?</div>', " ", chunk, flags=re.DOTALL)
    return chunk


def _named(chunk: str, known: frozenset[str]) -> set[str]:

    text = html.unescape(re.sub(r"<[^>]+>", " ", chunk))
    tokens = set(re.findall(r"[\w.-]+", text))
    return {ident for ident in known if ident in tokens}


def _titled(chunk: str, title: str) -> bool:
    text = html.unescape(chunk)
    return title in text or (len(title) > 24 and title[:24] in text)


def _bare(page: str, titles: dict[str, str]) -> dict[str, set[str]]:
    body = page[page.index("<body") :]
    known = frozenset(titles)
    found: dict[str, set[str]] = {}
    for name, chunk in REGION.findall(body):
        scanned = _without_shapes(chunk)
        missing = {ident for ident in _named(scanned, known) if not _titled(scanned, titles[ident])}
        if missing:
            found[name] = missing
    return found


@pytest.fixture
def doc() -> dict[str, Any]:
    return document("wall-v1.json")


def test_no_region_names_a_record_by_id_alone(doc: dict[str, Any]) -> None:

    doc["units"] = [
        *doc["units"],
        {"id": "basicly-parked1", "status": "deferred", "title": "parked work", "priority": "P3"},
    ]
    page = _wall()
    titles = board_regions.unit_titles(_reads(doc))
    assert titles, "the fixture carries no titled unit, so this proves nothing"
    bare = _bare(page, titles)
    assert not bare, "; ".join(
        f"region {name!r} names {sorted(ids)} with no title" for name, ids in sorted(bare.items())
    )


def test_the_gate_catches_a_region_that_drops_a_title(doc: dict[str, Any]) -> None:

    titles = board_regions.unit_titles(_reads(doc))
    ident, title = next(iter(titles.items()))
    faked = f'<body><section class="region ready"><span>{ident}</span></section></body>'
    caught = _bare(faked, {ident: title})
    assert caught == {"ready": {ident}}, "the gate does not see a bare id it is pointed at"


def test_a_declared_shape_element_is_the_only_exemption() -> None:

    assert SHAPE_ELEMENTS == ("chain",), (
        "a second element claims to draw a shape; that claim belongs in a review, not a diff"
    )


def test_an_id_the_board_draws_is_never_clipped_out_of_its_own_title_lookup() -> None:

    doc = document("wall-v1.json")
    longest = max((str(row["id"]) for row in doc["units"] if row.get("id")), key=len)
    assert len(longest) <= board_graph.ID_MAX, (
        f"{longest!r} is clipped by ID_MAX={board_graph.ID_MAX}, so the queue would print an "
        "id the title map cannot be keyed on"
    )


def _reads(doc: dict[str, Any]) -> Any:
    return board_wall.readings(doc, board_schema.verdict(REPO_ROOT, doc))

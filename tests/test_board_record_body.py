from __future__ import annotations

import re
from typing import Any

import pytest

from basicly import board_record, board_render
from tests.test_board_record_page import QUIET, TEMPLATES, _verdict
from tests.test_board_wall import STAMPED, document


@pytest.fixture
def doc() -> dict[str, Any]:
    return document("wall-v1.json")


def _record_page(doc: dict[str, Any], ident: str) -> str:
    filled = board_record.context(doc, _verdict(doc), ident, STAMPED)
    return "" if filled is None else board_render.render_record(filled, TEMPLATES)


def _linked(page: str, heading: str) -> list[str]:
    start = page.index(f"<h2>{heading}</h2>")
    after = start + len(f"<h2>{heading}</h2>")
    end = min(
        (page.index(m, after) for m in ("<h2>", "</main>") if m in page[after:]),
        default=len(page),
    )
    return re.findall(r'<a href="([\w.-]+)\.html">', page[after:end])


def test_no_link_section_names_a_record_by_id_alone() -> None:

    made = {
        "schema": "harness-board/v1",
        "generated_at": "2026-08-21T16:42:52Z",
        "units": [
            {"id": "basicly-self", "title": "the record under test", "status": "open"},
            {"id": "basicly-mum", "title": "the parent feature", "status": "open"},
            {"id": "basicly-kid", "title": "the child task", "status": "open"},
            {"id": "basicly-holds", "title": "the blocker", "status": "open"},
            {"id": "basicly-waits", "title": "the dependent", "status": "open"},
        ],
        "graph": {
            "edges": [
                {"from": "basicly-self", "kind": "parent-child", "to": "basicly-mum"},
                {"from": "basicly-kid", "kind": "parent-child", "to": "basicly-self"},
                {"from": "basicly-self", "kind": "blocks", "to": "basicly-holds"},
                {"from": "basicly-waits", "kind": "blocks", "to": "basicly-self"},
            ]
        },
    }
    page = _record_page(made, "basicly-self")
    titles = {str(r["id"]): str(r["title"]) for r in made["units"]}
    seen = 0
    for heading in ("the tree", "blocked by", "blocking"):
        named = _linked(page, heading)
        assert named, f"{heading} drew no record, so this section proves nothing"
        for ident in named:
            seen += 1
            assert titles[ident] in page, f"{heading} names {ident} with no title"
    assert seen == 4, f"expected the parent, the child and both edges; drew {seen}"


def test_the_page_renders_the_records_own_description(doc: dict[str, Any]) -> None:

    filled = board_record.context(
        doc,
        _verdict(doc),
        QUIET,
        STAMPED,
        page=board_record.PageFacts(body="## Acceptance Criteria\n\n- WHEN a, THE b SHALL c\n"),
    )
    assert filled is not None
    drawn = board_render.render_record(filled, TEMPLATES)
    assert "<h2>Acceptance Criteria</h2>" in drawn, "the description did not render as markdown"
    assert "WHEN a, THE b SHALL c" in drawn


def test_a_description_cannot_smuggle_markup_onto_the_page(doc: dict[str, Any]) -> None:
    filled = board_record.context(
        doc, _verdict(doc), QUIET, STAMPED, page=board_record.PageFacts(body="<script>x()</script>")
    )
    assert filled is not None
    drawn = board_render.render_record(filled, TEMPLATES)
    assert "<script>x()</script>" not in drawn
    assert "&lt;script&gt;" in drawn


def test_a_record_with_no_description_reads_as_an_absence(doc: dict[str, Any]) -> None:
    filled = board_record.context(doc, _verdict(doc), QUIET, STAMPED)
    assert filled is not None
    assert "carries no description" in board_render.render_record(filled, TEMPLATES)

from __future__ import annotations

import ast
import json
import re
import shutil
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from basicly import board_regions, board_render, board_schema, catalog_source
from tests.board_offline import assert_offline
from tests.test_board_wall import REPO_ROOT, STAMPED, document

TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"
SITE = REPO_ROOT / "site" / "index.html"
# fmt: off
SOURCES = (
    "board_render", "board_regions", "board_diagram", "board_graph",
    "board_loop", "board_footer", "board_record", "board_wall", "board_icons",
    "board_assets",
)
# fmt: on

REGIONS = ("head", "band", "loop", "flight", "ready", "foot", "tick")

_ROSTER = re.compile(r'<span class="miss state-\w+">([a-z_]+) —')

_DEFINED = re.compile(r"^\s*(--[a-z-]+):", re.MULTILINE)
_USED = re.compile(r"var\((--[a-z-]+)\)")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def render(name: str, *, root: Path = REPO_ROOT, after_s: float = 8.0) -> str:
    parsed = document(name)
    verdict = board_schema.verdict(root, parsed)
    return board_render.page(
        parsed, verdict, now=STAMPED + timedelta(seconds=after_s), templates_dir=TEMPLATES
    )


def _unchanged(schema: dict[str, Any]) -> None:
    pass


def _root_with_schema(tmp_path: Path, mutate: Any) -> Path:
    schemas = tmp_path / catalog_source.SCHEMAS_DIR
    shutil.copytree(REPO_ROOT / catalog_source.SCHEMAS_DIR, schemas)
    path = schemas / board_schema.SCHEMA_FILE
    schema = json.loads(path.read_text(encoding="utf-8"))
    mutate(schema)
    path.write_text(json.dumps(schema), encoding="utf-8")
    return tmp_path


def _literals(tree: ast.Module) -> list[str]:
    docstrings = {
        node.body[0].value
        for node in ast.walk(tree)
        if isinstance(node, (ast.Module, ast.ClassDef, ast.FunctionDef))
        and node.body
        and isinstance(node.body[0], ast.Expr)
        and isinstance(node.body[0].value, ast.Constant)
    }
    return [
        node.value
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and node not in docstrings
    ]


def test_the_page_references_no_external_origin() -> None:

    assert_offline(render("wall-v1.json"))


def test_the_freshness_sentence_is_drawn_once_for_the_whole_page() -> None:
    page = render("wall-v1.json")
    assert page.count('class="fresh') == 2
    assert page.count("2026-08-21T16:42:52Z") == 1
    assert page.count("stale after 60s") == 1


def test_the_alarm_colour_is_only_ever_the_watch_bands() -> None:

    page = render("wall-v1.json")
    styles = page.split("<style>", 1)[1].split("</style>", 1)[0]
    users = [
        selector.strip()
        for selector, body in _RULE.findall(styles)
        if "var(--orange)" in body or "--orange:" in body
    ]
    assert users, "no rule uses the alarm colour at all, so this probe proves nothing"
    for selector in users:
        assert ":root" in selector or ".band" in selector or ".state-stuck" in selector, (
            f"the alarm colour escaped the watch band: {selector}"
        )


def test_the_page_honours_prefers_reduced_motion() -> None:
    assert "@media (prefers-reduced-motion: reduce)" in render("wall-v1.json")


def test_the_css_names_every_state_the_code_declares() -> None:
    page = render("wall-v1.json")
    for state in board_render.board_wall.STATES:
        assert f".state-{state.key} {{" in page
        assert f"border-style: {state.border_style};" in page
        assert f"color: {state.colour};" in page


def test_no_row_of_the_wall_is_a_hand_tuned_pixel_and_one_column_below_1280px() -> None:

    page = render("dense-v1.json")
    stated = re.findall(r"grid-template-rows: ([^;]+);", page)
    assert stated, "the wall states no row list at all"
    for shape in stated:
        if shape.strip() == "none":
            continue
        assert shape.count("minmax(0, 1fr)") == 1, "no region absorbs the slack"
        assert re.search(r"[\d.]+(px|em|%)", shape) is None, f"a wall row is hand-tuned: {shape}"
        assert set(shape.split()) <= {"auto", "minmax(0,", "1fr)"}, f"a row is sized: {shape}"
    assert "@media (max-width: 1279px)" in page
    single = page.split("@media (max-width: 1279px)", 1)[1]
    assert "grid-template-columns: minmax(0, 1fr);" in single
    assert 'grid-template-areas: "head" "band" "loop"' in single


def test_no_region_draws_past_its_capacity_at_more_checks_than_the_tree_has() -> None:

    page = render("dense-v1.json")
    for marker in (
        "+2 more agents",
        "+2 more priorities",
        "+1 more lanes",
        "+6 more ready",
        "+4 more events",
        "+2 more waiting",
    ):
        assert marker in page, f"a population ran past its row with nothing said: {marker}"
    assert "more checks" not in page, "the gate grid is back, and so is the name that overlapped"
    assert "1 FAILING: pytest" in page, "the token that replaced it is not on the page"


@pytest.mark.parametrize(
    "fixture", ["wall-v1.json", "no-phase-v1.json", "minimal-v1.json", "dense-v1.json"]
)
def test_the_page_draws_whether_or_not_the_producer_populated_its_phases(fixture: str) -> None:

    page = render(fixture)
    for region in REGIONS:
        assert f'class="region {region}' in page, f"the {region} row is not drawn"
    expected = len(REGIONS) + (1 if _ROSTER.search(page) else 0)
    assert page.count('<section class="region') == expected


def test_the_running_row_gives_its_width_to_the_ready_list_when_no_lane_is_dispatched() -> None:

    busy = render("dense-v1.json")
    assert 'class="wall"' in busy, "the wall reflowed while seven lanes were running"
    assert busy.count('class="card ') == board_regions.FLIGHT_SLOTS
    drawn = busy.count('<td class="pri">') + busy.count('<tr class="feature">')
    assert drawn == board_regions.READY_SLOTS, "a group heading is a drawn line and spends a slot"

    calm = render("no-phase-v1.json")
    assert 'class="wall calm"' in calm, "an empty running row kept the width it was not using"
    assert 'class="card ' not in calm, "a card was drawn for a lane that does not exist"
    flight = calm.split('class="region flight', 1)[1].split("</section>", 1)[0]
    assert board_render.board_wall.ABSENT_TEXT in flight, "the collapsed row named no producer"


def test_a_wall_with_more_than_it_can_draw_says_how_much_more() -> None:
    page = render("wall-v1.json")
    for marker in ("+2 more waiting", "+6 more ready", "+4 more events"):
        assert marker in page


def test_an_absent_section_says_the_producer_did_not_emit_it() -> None:
    page = render("minimal-v1.json")
    assert board_render.board_wall.ABSENT_TEXT in page
    assert "ASKS NOT EMITTED" in page
    for section in ("session", "lanes", "asks", "gates", "spend", "health", "backlog", "units"):
        assert f'state-absent">{section} —' in page, f"{section} unnamed"


def test_the_roster_follows_the_schema_rather_than_the_layout(tmp_path: Path) -> None:

    control = _root_with_schema(tmp_path / "control", _unchanged)
    assert len(_ROSTER.findall(render("minimal-v1.json", root=control))) == 13

    added = _root_with_schema(
        tmp_path / "added", lambda schema: schema["properties"].update(invented={"type": "object"})
    )
    page = render("minimal-v1.json", root=added)
    assert _ROSTER.findall(page).count("invented") == 1
    assert len(_ROSTER.findall(page)) == 14

    dropped = _root_with_schema(
        tmp_path / "dropped", lambda schema: schema["properties"].pop("events")
    )
    page = render("minimal-v1.json", root=dropped)
    assert len(_ROSTER.findall(page)) == 12
    assert "events" not in _ROSTER.findall(page)


def test_the_renderer_imports_nothing_that_could_read_engine_state() -> None:

    allowed = {*SOURCES, "board_fields", "catalog"}
    for name in SOURCES:
        source = (REPO_ROOT / "src" / "basicly" / f"{name}.py").read_text(encoding="utf-8")
        imported = {
            alias.name
            for node in ast.walk(ast.parse(source))
            if isinstance(node, ast.ImportFrom) and node.level == 1 and node.module is None
            for alias in node.names
        }
        assert imported <= allowed, f"{name} imports {sorted(imported - allowed)}"
        for literal in _literals(ast.parse(source)):
            assert ".basicly/" not in literal, f"{name} carries an engine path: {literal}"

"""The page: what it may reference, what it must say about its own age, and what it refuses.

The claim under test is not "a template rendered". These are the properties the whole design
turns on, and each is asserted against a *refutation*:

* **Self-contained.** A page that fetches anything is blank on the wall the day the network is
  down, so the absence of an external reference is asserted, not the presence of a stylesheet.
* **The freshness sentence appears once.** The render this replaces printed it on all ten
  panels, so the count is the assertion - one reading for the page, not one per region.
* **The inventory is the schema's.** Asserted by *moving the schema* - a property added to a
  copy must move the roster, and one removed must move it back - which is the only assertion a
  second hand-written section list could not also satisfy.
* **The layout is fixed and reflows once.** Fixed rows at 1920x1080 and a single column below
  1280px, both read off the rendered CSS.
* **The producer's gaps do not break it.** Two fixtures render: one with `units[].phase` and
  `ready` populated, one with neither. The second is what the reference producer emitted when
  this was written, so the layout had to be correct before the producer caught up.

What a **picture** shows and this file cannot is legibility, and the two screenshots the unit
was reviewed against are the record of that. These assertions are what stops a regression.
"""

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
from tests.test_board_wall import REPO_ROOT, STAMPED, document

TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"
SITE = REPO_ROOT / "site" / "index.html"
SOURCES = ("board_render", "board_regions", "board_footer", "board_wall")

# The eight fixed rows of the wall, in the order the grid declares them.
REGIONS = ("head", "band", "loop", "flight", "ready", "foot", "tick", "inv")

# One roster chip, and the section it names. Only the roster matches: the key beneath it wraps
# its glyph onto its own line, so a count taken with this pattern is the section count.
_ROSTER = re.compile(r'<span class="chip state-\w+">. ([a-z_]+)</span>')

_DEFINED = re.compile(r"^\s*(--[a-z-]+):", re.MULTILINE)
_USED = re.compile(r"var\((--[a-z-]+)\)")
_RULE = re.compile(r"([^{}]+)\{([^{}]*)\}")


def render(name: str, *, root: Path = REPO_ROOT, after_s: float = 8.0) -> str:
    """One fixture drawn as the page, against an injected instant."""
    parsed = document(name)
    verdict = board_schema.verdict(root, parsed)
    return board_render.page(
        parsed, verdict, now=STAMPED + timedelta(seconds=after_s), templates_dir=TEMPLATES
    )


def _unchanged(schema: dict[str, Any]) -> None:
    """The control mutation: the shipped schema, copied and not touched."""


def _root_with_schema(tmp_path: Path, mutate: Any) -> Path:
    """A repo root carrying a *copy* of the shipped schema, with *mutate* applied to it."""
    schemas = tmp_path / catalog_source.SCHEMAS_DIR
    shutil.copytree(REPO_ROOT / catalog_source.SCHEMAS_DIR, schemas)
    path = schemas / board_schema.SCHEMA_FILE
    schema = json.loads(path.read_text(encoding="utf-8"))
    mutate(schema)
    path.write_text(json.dumps(schema), encoding="utf-8")
    return tmp_path


def _literals(tree: ast.Module) -> list[str]:
    """Every string constant in *tree* that is not a docstring."""
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
    """No fetch of any kind: not a script, not a stylesheet, not an image.

    Asserted on the raw text rather than by parsing, because the failure this guards is a
    template gaining an attribute a parser would have to be taught about first.
    """
    page = render("wall-v1.json")
    assert "<script" not in page
    assert "<link" not in page
    assert "src=" not in page
    assert "http://" not in page
    assert "https://" not in page


def test_the_freshness_sentence_is_drawn_once_for_the_whole_page() -> None:
    """The named defect: the render this replaces repeated it verbatim on all ten panels."""
    page = render("wall-v1.json")
    assert page.count("as of 8s ago") == 1
    assert page.count("2026-08-21T16:42:52Z") == 1
    assert page.count("stale after 60s") == 1


def test_the_page_uses_only_the_palette_the_site_already_ships() -> None:
    """The board looks like basicly because it lifts `site/index.html`, not because it tried.

    Both directions: no custom property the site does not define, and no `var()` the page does
    not define - an undefined `var()` renders as nothing and is invisible in review.
    """
    page = render("wall-v1.json")
    site = set(_DEFINED.findall(SITE.read_text(encoding="utf-8")))
    defined = set(_DEFINED.findall(page))
    assert defined <= site, f"invented custom properties: {sorted(defined - site)}"
    assert set(_USED.findall(page)) <= defined


def test_the_alarm_colour_is_only_ever_the_watch_bands() -> None:
    """`site/index.html` ships no red, so orange is the alarm - and the band is its only site.

    Read off the rendered CSS rather than off the palette, because the rule is about *where*
    the hue is used. Every selector mentioning `--orange` must be a band selector.
    """
    page = render("wall-v1.json")
    styles = page.split("<style>", 1)[1].split("</style>", 1)[0]
    users = [
        selector.strip()
        for selector, body in _RULE.findall(styles)
        if "var(--orange)" in body or "--orange:" in body
    ]
    assert users, "no rule uses the alarm colour at all, so this probe proves nothing"
    for selector in users:
        assert ":root" in selector or ".band" in selector or ".state-waiting" in selector, (
            f"the alarm colour escaped the watch band: {selector}"
        )


def test_the_page_honours_prefers_reduced_motion() -> None:
    """Inherited from `site/index.html`, which already honours it."""
    assert "@media (prefers-reduced-motion: reduce)" in render("wall-v1.json")


def test_the_css_names_every_state_the_code_declares() -> None:
    """Generated from `board_wall.STATES`, so the CSS cannot fall behind the model."""
    page = render("wall-v1.json")
    for state in board_render.board_wall.STATES:
        assert f".state-{state.key} {{" in page
        assert f"border-style: {state.border_style};" in page
        assert f"color: {state.colour};" in page


def test_no_row_of_the_wall_is_a_hand_tuned_pixel_and_one_column_below_1280px() -> None:
    """The defect this file's row assertion could not see, asserted as its own property.

    The row list used to be six stated pixel heights, measured against the tallest content of
    the day. That catches a row *losing* its height and cannot catch a row going one line short
    of its content, which is what happened when the gate strip went from 12 checks to 36: the
    footer clipped `health` and the loop row cut its caption to a partial line. So what is
    asserted is that no row states a length at all - each is the height of what it holds, and
    what it holds is bounded by the capacities :func:`test_no_region_draws_past_its_capacity`
    covers.

    **Both row lists**, because the running row now collapses and the wall has two shapes; a
    check that read only the first would let the collapsed one state whatever it liked.
    """
    page = render("dense-v1.json")
    stated = re.findall(r"grid-template-rows: ([^;]+);", page)
    assert len(stated) >= 2, "the collapsed shape declares no rows of its own"
    for shape, regions in zip(stated[:2], (len(REGIONS) - 2, len(REGIONS) - 1), strict=True):
        # `flight` and `ready` share the slack row while a lane runs and separate when none
        # does, so the count of content-bounded rows moves by one and the slack row never does.
        assert shape.count("minmax(0, 1fr)") == 1, "no region absorbs the slack"
        assert shape.count("auto") == regions, "a row is not bounded by what it holds"
        assert re.search(r"[\d.]+(px|em|%)", shape) is None, f"a wall row is hand-tuned: {shape}"
    assert "@media (max-width: 1279px)" in page
    single = page.split("@media (max-width: 1279px)", 1)[1]
    assert "grid-template-columns: minmax(0, 1fr);" in single
    assert 'grid-template-areas: "head" "band" "loop"' in single


def test_no_region_draws_past_its_capacity_at_more_checks_than_the_tree_has() -> None:
    """The other half of the fix: a bounded row is only safe over bounded content.

    The fixture carries 40 gate checks against the tree's 36, six agents, ten priority labels,
    seven lanes and five events, so every capped population on the page is over its cap at once
    - which is the arrangement the hand-tuned rows were never rendered against. Each cap has to
    report what it dropped, because a row that is the height of its content will happily be the
    height of *all* of it and push the region below off the screen.

    The gate set is the one population with no marker left, and its absence is the assertion:
    it no longer has a capacity to run past, because 40 checks and 13 draw the same one token.

    What a picture shows and this cannot is that the result fits 1080px; the screenshots the
    unit was reviewed against are that record.
    """
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
    """Built against the schema, not against one producer's current output.

    `units[].phase` and `ready` were null on every row when this was written, and `lanes` and
    `session` were not emitted at all. The layout had to be right before the producer caught
    up, so all three fixtures draw the same seven regions.
    """
    page = render(fixture)
    assert page.count('<section class="region') == len(REGIONS)
    for region in REGIONS:
        assert f'class="region {region}' in page, f"the {region} row is not drawn"


def test_the_running_row_gives_its_width_to_the_ready_list_when_no_lane_is_dispatched() -> None:
    """The page's two shapes, and the state the wall is in most of the day is the second.

    Asserted on the class the grid switches on *and* on the row count that follows it, because
    those are the two halves that have to agree: a page that reflowed the grid and still drew
    eight rows would leave the reclaimed height blank, and one that drew fourteen without
    reflowing would put them in the 470px column.
    """
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
    """No content is cut without a marker naming what was dropped."""
    page = render("wall-v1.json")
    for marker in ("+2 more waiting", "+6 more ready", "+4 more events"):
        assert marker in page


def test_an_absent_section_says_the_producer_did_not_emit_it() -> None:
    """A zero or an empty box claims a measurement. The minimal fixture is the whole case."""
    page = render("minimal-v1.json")
    assert board_render.board_wall.ABSENT_TEXT in page
    assert "ASKS NOT EMITTED" in page
    # Every section this fixture omits, named on the roster with the state that says so.
    for section in ("session", "lanes", "asks", "gates", "spend", "health", "backlog", "units"):
        assert f'state-absent">\N{WHITE CIRCLE} {section}</span>' in page, f"{section} unnamed"


def test_the_roster_follows_the_schema_rather_than_the_layout(tmp_path: Path) -> None:
    """Move the schema and the page must move with it, in both directions.

    The control is the unmodified copy: a roster that did not depend on the schema at all would
    agree with the first assertion and fail only these two.
    """
    control = _root_with_schema(tmp_path / "control", _unchanged)
    assert len(_ROSTER.findall(render("minimal-v1.json", root=control))) == 12

    added = _root_with_schema(
        tmp_path / "added", lambda schema: schema["properties"].update(invented={"type": "object"})
    )
    page = render("minimal-v1.json", root=added)
    assert _ROSTER.findall(page).count("invented") == 1
    assert len(_ROSTER.findall(page)) == 13

    dropped = _root_with_schema(
        tmp_path / "dropped", lambda schema: schema["properties"].pop("events")
    )
    page = render("minimal-v1.json", root=dropped)
    assert len(_ROSTER.findall(page)) == 11
    assert "events" not in _ROSTER.findall(page)


def test_the_renderer_imports_nothing_that_could_read_engine_state() -> None:
    """The structural half is `.importlinter`'s forbidden contract; this is the narrow half.

    A module reachable only through a function-level import would satisfy the tier stack and
    still let a consumer read the ledger, so the import block itself is asserted - on all four
    modules, because the extraction that produced them is exactly how such an import hides.
    """
    for name in SOURCES:
        source = (REPO_ROOT / "src" / "basicly" / f"{name}.py").read_text(encoding="utf-8")
        for imported in re.findall(r"^from \. import (.+)$", source, re.MULTILINE):
            assert set(imported.split(", ")) <= {*SOURCES, "board_fields", "catalog"}
        # Literals only. A module's own prose names the directories it refuses, so a text scan
        # finds its docstring and reports the refusal as the violation.
        for literal in _literals(ast.parse(source)):
            assert ".basicly/" not in literal, f"{name} carries an engine path: {literal}"

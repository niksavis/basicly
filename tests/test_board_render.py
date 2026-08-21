"""The page: what it may reference, what it must say about its own age, and what it refuses.

The claim under test is not "a template rendered". Three properties are the whole point of
this renderer and each is asserted against a *refutation* rather than a happy path:

* **Self-contained.** A page that fetches anything is a page that is blank on the wall the
  day the network is down, so the absence of an external reference is asserted, not the
  presence of a stylesheet.
* **No value without its age.** Every panel carries the document's stamp and a computed age.
  The test counts panels against freshness lines, because a page that grew a thirteenth panel
  without one is exactly the drift the rule exists to stop.
* **The inventory is the schema's.** The section list is asserted by *moving the schema* - a
  property added to a copy must move the panel count, and a property removed must move it
  back - which is the only assertion a second hand-written list could not also satisfy.
"""

from __future__ import annotations

import ast
import json
import re
import shutil
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from basicly import board_render, board_schema, catalog_source

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board"
TEMPLATES = REPO_ROOT / ".basicly" / "core" / "templates" / "board"
SITE = REPO_ROOT / "site" / "index.html"

# The document's own instant, so an age is a function of the fixture and not of the clock.
STAMPED = datetime(2026, 8, 14, 16, 42, 52, tzinfo=UTC)

_PANEL = re.compile(
    r'<div class="panel state-(\w+)">(.*?)(?=<div class="panel |</section>)', re.DOTALL
)
_DEFINED = re.compile(r"^\s*(--[a-z-]+):", re.MULTILINE)
_USED = re.compile(r"var\((--[a-z-]+)\)")


def _document(name: str) -> dict[str, Any]:
    return json.loads((FIXTURES / name).read_text(encoding="utf-8"))


def _render(document: dict[str, Any], *, root: Path = REPO_ROOT, after_s: float = 0.0) -> str:
    verdict = board_schema.verdict(root, document)
    return board_render.page(
        document, verdict, now=STAMPED + timedelta(seconds=after_s), templates_dir=TEMPLATES
    )


def _panels(page: str) -> list[tuple[str, str]]:
    return _PANEL.findall(page)


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
    page = _render(_document("full-v1.json"))
    assert "<script" not in page
    assert "<link" not in page
    assert "src=" not in page
    assert "http://" not in page
    assert "https://" not in page


def test_every_panel_names_the_age_of_the_value_it_draws() -> None:
    """The honesty rule, counted: one freshness line per panel, and the stamp inside it."""
    page = _render(_document("full-v1.json"), after_s=8)
    panels = _panels(page)
    assert len(panels) == 12, "a panel per optional section the schema declares"
    for state, body in panels:
        assert "as of 8s ago" in body, f"the {state} panel drew a value with no age"
        assert "2026-08-14T16:42:52Z" in body


def test_a_document_older_than_its_own_bound_reads_stale() -> None:
    """`stale_after_s` is the producer's, and the page honours it in both directions."""
    fresh = board_render.age(_document("full-v1.json"), STAMPED + timedelta(seconds=30))
    old = board_render.age(_document("full-v1.json"), STAMPED + timedelta(seconds=90))
    assert fresh.state.key == board_render.LIVE
    assert old.state.key == board_render.STALE
    assert old.phrase == "1m 30s ago"


def test_an_undatable_document_is_stale_rather_than_blank() -> None:
    """A viewer that cannot date the file it draws has no grounds to call it live."""
    broken = {**_document("minimal-v1.json"), "generated_at": "not a stamp"}
    assert board_render.age(broken, STAMPED).state.key == board_render.STALE
    assert board_render.age(broken, STAMPED).phrase == "age unknown"


def test_the_page_uses_only_the_palette_the_site_already_ships() -> None:
    """The board looks like basicly because it lifts `site/index.html`, not because it tried.

    Both directions: no custom property the site does not define, and no `var()` the page
    does not define - an undefined `var()` renders as nothing and is invisible in review.
    """
    page = _render(_document("full-v1.json"))
    site = set(_DEFINED.findall(SITE.read_text(encoding="utf-8")))
    defined = set(_DEFINED.findall(page))
    assert defined <= site, f"invented custom properties: {sorted(defined - site)}"
    assert set(_USED.findall(page)) <= defined


def test_the_page_honours_prefers_reduced_motion() -> None:
    """Inherited from `site/index.html`, which already honours it."""
    page = _render(_document("full-v1.json"))
    assert "@media (prefers-reduced-motion: reduce)" in page


def test_every_state_is_encoded_on_a_glyph_and_a_border_as_well_as_colour() -> None:
    """Three channels, and the two non-colour ones must actually discriminate.

    The pairwise assertion is the part that matters: three states rendered with one glyph and
    one border style would satisfy "has a glyph" and tell a colour-blind reader nothing.
    """
    page = _render(_document("full-v1.json"))
    for state in board_render.STATES:
        assert f".state-{state.key} {{" in page
        assert f"border-style: {state.border_style};" in page
        assert f"color: {state.colour};" in page
    panelled = [board_render._BY_KEY[key] for key in ("renderable", "withheld", "absent")]
    assert len({(state.glyph, state.border_style) for state in panelled}) == len(panelled)


def test_an_absent_section_says_the_producer_did_not_emit_it() -> None:
    """A zero or an empty box claims a measurement. The foreign fixture is the whole case."""
    page = _render(_document("minimal-v1.json"))
    panels = _panels(page)
    assert [state for state, _ in panels] == [board_render.ABSENT] * 12
    assert page.count(board_render.ABSENT_TEXT) == 12
    assert page.count('<section class="region"') == 8, "the eight wall regions still draw"


def test_a_broken_section_is_withheld_and_names_its_violations() -> None:
    """The rest of the board draws, and the panel says why it did not."""
    document = _document("broken-section-v1.json")
    verdict = board_schema.verdict(REPO_ROOT, document)
    assert verdict.withheld, "the fixture no longer carries a non-conformant section"
    page = _render(document)
    withheld = [body for state, body in _panels(page) if state == board_render.WITHHELD]
    assert len(withheld) == len(verdict.withheld)
    assert all("$." in body for body in withheld), "a withheld panel named no violation"


@pytest.mark.parametrize(
    ("part", "whole"),
    [(None, 100), (100, None), (100, 0), ("many", 100), (100, True), (True, 100)],
)
def test_a_bar_is_refused_when_either_term_is_absent_or_unmeasured(
    part: object, whole: object
) -> None:
    """The raw number instead, and the refusal is the default rather than a special case.

    This repo has already shipped a wrong `context_window`; a bar drawn against a wrong
    ceiling reads as reassurance, which is worse than the number it replaced.
    """
    assert board_render.bar(part, whole) is None


def test_a_bar_over_its_whole_says_so_rather_than_capping_silently() -> None:
    """The catastrophe signal: 4449% is the number an operator has to see."""
    drawn = board_render.bar(177_970_761, 4_000_000)
    assert drawn is not None
    assert drawn.label == "4449%"
    assert drawn.width == 100.0
    assert drawn.over


def test_a_section_missing_a_bar_term_renders_the_number_and_no_bar() -> None:
    """The state-driven half of the bar rule, asserted on the page rather than the helper."""
    document = _document("full-v1.json")
    document["backlog"].pop("total")
    page = _render(document)
    backlog = next(body for state, body in _panels(page) if "backlog" in body[:200])
    assert "closed" in backlog
    assert 'class="track"' not in backlog


def test_the_panel_inventory_follows_the_schema_rather_than_the_layout(tmp_path: Path) -> None:
    """Move the schema and the page must move with it, in both directions.

    The control is the unmodified copy: a count that did not depend on the schema at all
    would agree with the first assertion and fail only these two.
    """
    document = _document("minimal-v1.json")

    control = _root_with_schema(tmp_path / "control", _unchanged)
    assert len(_panels(_render(document, root=control))) == 12

    added = _root_with_schema(
        tmp_path / "added", lambda schema: schema["properties"].update(invented={"type": "object"})
    )
    page = _render(document, root=added)
    assert len(_panels(page)) == 13
    assert "invented" in page

    dropped = _root_with_schema(
        tmp_path / "dropped", lambda schema: schema["properties"].pop("gates")
    )
    page = _render(document, root=dropped)
    assert len(_panels(page)) == 11
    assert "gates" in board_render.LAYOUT[4][2], "the layout still names the dropped section"
    assert ">gates\n" not in page and ">gates<" not in page


def test_the_renderer_imports_nothing_that_could_read_engine_state() -> None:
    """The structural half is `.importlinter`'s forbidden contract; this is the narrow half.

    A module reachable only through a function-level import would satisfy the tier stack and
    still let this module read the ledger, so the import block itself is asserted.
    """
    module = REPO_ROOT / "src" / "basicly" / "board_render.py"
    source = module.read_text(encoding="utf-8")
    assert re.findall(r"^from \. import (.+)$", source, re.MULTILINE) == ["board_fields, catalog"]
    # Literals only. The module's own prose names both directories to say it refuses them, so
    # a text scan finds its own docstring and reports the refusal as the violation.
    for literal in _literals(ast.parse(source)):
        assert ".basicly/" not in literal, f"the renderer carries an engine path: {literal}"

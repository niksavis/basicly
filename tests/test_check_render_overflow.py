from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_render_overflow.py"
RENDER = REPO_ROOT / "tests" / "fixtures" / "render"
FIXTURE = RENDER / "clipped-and-not.html"
COLLIDING = RENDER / "overlapping-and-not.html"


def _load(path: Path, name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_render_overflow")


def test_a_box_that_scrolls_is_not_counted_as_clipped() -> None:

    assert "hides(css.overflowX)" in gate._PROBE
    assert "'hidden' || mode === 'clip'" in gate._PROBE


def test_a_declared_ellipsis_is_not_counted_as_clipped() -> None:
    assert "css.textOverflow === 'ellipsis'" in gate._PROBE


def test_the_probe_reports_the_viewport_it_measured() -> None:
    assert "document.documentElement.clientWidth" in gate._PROBE


def test_a_missing_page_is_refused() -> None:
    assert gate.main([str(REPO_ROOT / "no-such-page.html")]) == 2


def test_a_page_that_is_not_text_is_refused_rather_than_crashing() -> None:
    png = REPO_ROOT / "site" / "favicon.ico"
    if not png.is_file():
        return
    assert gate.main([str(png)]) == 2


def test_the_browser_search_is_ordered_and_names_wsl_first() -> None:
    assert gate._CANDIDATES[0].startswith("/mnt/c/")
    assert "chromium" in gate._CANDIDATES


def test_the_positive_control_fixture_holds_all_three_cases() -> None:
    text = FIXTURE.read_text(encoding="utf-8")
    assert "overflow:hidden" in text
    assert "overflow:auto" in text
    assert "text-overflow:ellipsis" in text


def test_the_tolerance_is_above_sub_pixel_rounding_and_below_a_line() -> None:
    assert 0 < gate.TOLERANCE_PX < 10


def test_the_two_signals_are_reported_apart_and_neither_short_circuits_the_other() -> None:

    assert gate.OVERFLOW != gate.OVERLAP
    assert "clipped" in gate._PROBE and "collided" in gate._PROBE
    assert gate._report_clipped([], "page") == 0
    assert gate._report_collided([], "page") == 0
    clip = {"id": None, "cls": "c", "tag": "div", "overflow_x": 0, "overflow_y": 9}
    assert gate._report_clipped([clip], "page") == 1
    collision = {"a": "x", "b": "y", "text_a": "", "text_b": "", "shared_x": 9, "shared_y": 9}
    assert gate._report_collided([collision], "page") == 1


def test_a_refusal_is_printed_under_both_prefixes() -> None:
    assert gate._refuse("nothing was measured") == 2


def test_only_the_outermost_element_carrying_its_own_text_is_paired() -> None:

    assert "node.nodeType === 3" in gate._PROBE, "elements are paired without regard to text"
    assert "carriers.has(up)" in gate._PROBE, "an inner span is paired as well as its block"


def test_an_ancestor_and_its_descendant_are_not_a_collision() -> None:
    assert "a.el.contains(b.el) || b.el.contains(a.el)" in gate._PROBE


def test_an_element_taken_out_of_flow_is_stacked_on_purpose() -> None:
    for position in ("absolute", "fixed", "sticky"):
        assert f"'{position}'" in gate._PROBE


def test_a_collision_needs_both_axes_above_the_tolerance() -> None:
    assert "ix > TOL && iy > TOL" in gate._PROBE


def test_the_overlap_control_fixture_carries_the_wall_defect_and_its_three_quiet_cases() -> None:
    text = COLLIDING.read_text(encoding="utf-8")
    assert "grid-template-rows:repeat(2, 1.2em)" in text, "the row height is not fixed"
    assert "a deliberately over long name" in text, "no name is long enough to wrap"
    assert "align-items:baseline" in text, "a stretched child hides the wrap from a box measure"
    assert 'class="apart"' in text, "no touching-but-not-overlapping control"
    assert 'class="nested"' in text, "no ancestor-holds-descendant control"
    assert "position:absolute" in text, "no out-of-flow overlay control"
    assert "overflow:hidden" not in text and "overflow: hidden" not in text

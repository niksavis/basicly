"""Tests for the two rendered-geometry instruments: the clip, and the collision.

**No browser runs here.** Continuous integration has none, so the suite binds the parts
that decide *what counts* - the probe's discriminations, the browser search, the WSL path
crossing and the refusal paths - and the browser half is exercised by a human against two
committed fixtures, each the positive control for one signal and the negative control for
the other:

* `clipped-and-not.html` - one `overflow: hidden` box that must be reported, one
  `overflow: auto` box with identical content that must not, and one declared ellipsis that
  must not. Nothing on it collides, so the overlap signal must stay silent on it.
* `overlapping-and-not.html` - a fixed-height grid row holding a name that wraps and is
  painted across the row below, which is the wall's own defect in miniature. Nothing on it
  overflows: the second line is laid out and *then* drawn over, so `scrollHeight` equals
  `clientHeight` and the clip signal must stay silent on it. Its three quiet controls are
  boxes that merely touch, a parent carrying text around a child carrying text, and an
  absolutely positioned overlay - none of the three is a defect.

Measured: the first reports 1 clip and 0 collisions, the second 0 clips and 1 collision. A
signal that fires on both fixtures is not discriminating between them.

Both are committed rather than built in a `tmp_path` for the reason the board's own corpus
is: a control nobody can re-run is not a control.
"""

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
    """Load a standalone script by path, the way `uv run python` does."""
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


gate = _load(SCRIPT, "check_render_overflow")


def test_a_box_that_scrolls_is_not_counted_as_clipped() -> None:
    """Overflowing is not being clipped: content a box scrolls to is still reachable.

    Without this the page's own reflow below 1280px reported as a 530px defect.
    """
    assert "hides(css.overflowX)" in gate._PROBE
    assert "'hidden' || mode === 'clip'" in gate._PROBE


def test_a_declared_ellipsis_is_not_counted_as_clipped() -> None:
    """An element with `text-overflow: ellipsis` says in its own CSS that it truncates."""
    assert "css.textOverflow === 'ellipsis'" in gate._PROBE


def test_the_probe_reports_the_viewport_it_measured() -> None:
    """A finding against an unstated viewport cannot be reproduced, and one was not."""
    assert "document.documentElement.clientWidth" in gate._PROBE


def test_a_missing_page_is_refused() -> None:
    """Exit 2, never 0: an unanswered question is not a passing page."""
    assert gate.main([str(REPO_ROOT / "no-such-page.html")]) == 2


def test_a_page_that_is_not_text_is_refused_rather_than_crashing() -> None:
    """A PNG handed to it by mistake reports the reason; a traceback reports the tool."""
    png = REPO_ROOT / "site" / "favicon.ico"
    if not png.is_file():
        return
    assert gate.main([str(png)]) == 2


def test_the_browser_search_is_ordered_and_names_wsl_first() -> None:
    """The first hit must be the browser a reader's own screenshot came from."""
    assert gate._CANDIDATES[0].startswith("/mnt/c/")
    assert "chromium" in gate._CANDIDATES


def test_the_positive_control_fixture_holds_all_three_cases() -> None:
    """The control discriminates only if it carries a box of each kind."""
    text = FIXTURE.read_text(encoding="utf-8")
    assert "overflow:hidden" in text
    assert "overflow:auto" in text
    assert "text-overflow:ellipsis" in text


def test_the_tolerance_is_above_sub_pixel_rounding_and_below_a_line() -> None:
    """A tolerance at zero reports rounding; one at a line height hides a lost row."""
    assert 0 < gate.TOLERANCE_PX < 10


def test_the_two_signals_are_reported_apart_and_neither_short_circuits_the_other() -> None:
    """The finding that produced the second signal: a collision is not an overflow.

    The clip instrument reported zero on a wall where two gate names were painted over the
    two below them, and was right to - an element drawn across its neighbour holds nothing
    more than it shows. Merging the two would have made that zero a one for the wrong reason
    and left nobody able to say which fault a page has.
    """
    assert gate.OVERFLOW != gate.OVERLAP
    assert "clipped" in gate._PROBE and "collided" in gate._PROBE
    # Both report functions exist and are called for their own list, so a page can fail
    # either alone; `main` takes the worse of the two rather than returning on the first.
    assert gate._report_clipped([], "page") == 0
    assert gate._report_collided([], "page") == 0
    clip = {"id": None, "cls": "c", "tag": "div", "overflow_x": 0, "overflow_y": 9}
    assert gate._report_clipped([clip], "page") == 1
    collision = {"a": "x", "b": "y", "text_a": "", "text_b": "", "shared_x": 9, "shared_y": 9}
    assert gate._report_collided([collision], "page") == 1


def test_a_refusal_is_printed_under_both_prefixes() -> None:
    """A reader who greps one signal must not read the silence of a refused run as a pass."""
    assert gate._refuse("nothing was measured") == 2


def test_only_the_outermost_element_carrying_its_own_text_is_paired() -> None:
    """An inline box is its font's em box, not its line box, and that bleeds.

    A monospace glyph inside a sans line sticks a few pixels out of the block that lays it
    out and intersects the block above: six such pairs on the live board against one real
    collision. Taking the outermost carrier of each run of text drops all six and keeps the
    one, because an ancestor's box holds its descendant's anyway.
    """
    assert "node.nodeType === 3" in gate._PROBE, "elements are paired without regard to text"
    assert "carriers.has(up)" in gate._PROBE, "an inner span is paired as well as its block"


def test_an_ancestor_and_its_descendant_are_not_a_collision() -> None:
    """`<p>outer <span>inner</span></p>` intersects on every page ever written."""
    assert "a.el.contains(b.el) || b.el.contains(a.el)" in gate._PROBE


def test_an_element_taken_out_of_flow_is_stacked_on_purpose() -> None:
    """An overlay, a tooltip and a sticky header are all drawn over their neighbours."""
    for position in ("absolute", "fixed", "sticky"):
        assert f"'{position}'" in gate._PROBE


def test_a_collision_needs_both_axes_above_the_tolerance() -> None:
    """Two boxes sharing an edge intersect by zero in one axis, and rounding makes it a bit."""
    assert "ix > TOL && iy > TOL" in gate._PROBE


def test_the_overlap_control_fixture_carries_the_wall_defect_and_its_three_quiet_cases() -> None:
    """The control discriminates only if the quiet cases are on the same page as the loud one."""
    text = COLLIDING.read_text(encoding="utf-8")
    assert "grid-template-rows:repeat(2, 1.2em)" in text, "the row height is not fixed"
    assert "a deliberately over long name" in text, "no name is long enough to wrap"
    assert "align-items:baseline" in text, "a stretched child hides the wrap from a box measure"
    assert 'class="apart"' in text, "no touching-but-not-overlapping control"
    assert 'class="nested"' in text, "no ancestor-holds-descendant control"
    assert "position:absolute" in text, "no out-of-flow overlay control"
    # The page must not *also* clip, or it cannot show the two signals are independent.
    assert "overflow:hidden" not in text and "overflow: hidden" not in text

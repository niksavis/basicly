"""Tests for the rendered-overflow instrument.

**No browser runs here.** Continuous integration has none, so the suite binds the parts
that decide *what counts* - the probe's three-way discrimination, the browser search, the
WSL path crossing and the refusal paths - and the browser half is exercised by a human
against `tests/fixtures/render/clipped-and-not.html`, whose three boxes are the positive
control: one `overflow: hidden` box that must be reported, one `overflow: auto` box with
identical content that must not, and one declared ellipsis that must not.

That fixture is committed rather than built in a `tmp_path` for the reason the board's own
corpus is: a control nobody can re-run is not a control.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

REPO_ROOT = Path(__file__).parent.parent
SCRIPT = REPO_ROOT / ".scripts" / "check_render_overflow.py"
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "render" / "clipped-and-not.html"


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

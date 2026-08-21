#!/usr/bin/env python3
"""Report every element of a rendered page whose content overflows its own box.

A green check set cannot see a clip. Three board pages shipped in one day with every
gate passing and a region cut off the screen: a schema dump nobody could read, five
clipped regions - four already clipped on the fixture the layout passed against - and a
panel drawing `0` over ten real edges. Each was found by looking at a screenshot, which
is a human act that does not scale and does not survive a handover.

**A scrollbar is not the signal.** A wall page sets `overflow: hidden`, so it clips in
silence and the absence of a scrollbar proves nothing. The measurement is the DOM's own:
an element whose `scrollWidth`/`scrollHeight` exceeds its `clientWidth`/`clientHeight`
holds more than it shows.

Deliberately **not** a `[[verify.checks]]` entry: continuous integration has no browser,
and a check that skips reads as a pass - this repository's own failure-semantics table
says so. It is a script a human and an agent run, and `rendered-surfaces` is the rule
that says when.

    uv run python .scripts/check_render_overflow.py board.html
    uv run python .scripts/check_render_overflow.py board.html --width 1200 --height 900
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404 - a browser is the only instrument that can answer this
import sys
import tempfile
from pathlib import Path

# Where a Windows browser lives when this runs under WSL, and the POSIX names otherwise.
# Ordered, because the first that exists is the one a reader's own screenshot came from.
_CANDIDATES = (
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
)

# Sub-pixel rounding makes a scroll size exceed a client size by a fraction on a box that
# fits. Measured against a page with no clip at all: the largest honest delta was under
# one device pixel, so anything at or below this is the renderer's arithmetic, not a clip.
TOLERANCE_PX = 2

# The probe writes its answer here rather than to the console, because `--dump-dom` gives
# back the document and never the log.
_MARKER = "data-overflow-report"

_PROBE = """
<script>
window.addEventListener('load', function () {
  var out = [];
  document.querySelectorAll('*').forEach(function (el) {
    var css = getComputedStyle(el);
    var dx = el.scrollWidth - el.clientWidth;
    var dy = el.scrollHeight - el.clientHeight;
    // Overflowing is not being clipped. Content the box scrolls to, or lets spill, is
    // still reachable; only `hidden` and `clip` take it away. Without this the page's
    // own reflow scroll below 1280px reported as a 530px defect.
    var hides = function (mode) { return mode === 'hidden' || mode === 'clip'; };
    if (!hides(css.overflowX)) { dx = 0; }
    if (!hides(css.overflowY)) { dy = 0; }
    // An ellipsis is a declared truncation, not a clip: the element is *meant* to hold
    // more than it shows and says so in its own CSS. Counting it drowns the real finding
    // - the live board reported eight of these against one genuine overflow.
    if (css.textOverflow === 'ellipsis') { dx = 0; }
    if (dx > TOL || dy > TOL) {
      out.push({
        tag: el.tagName.toLowerCase(),
        id: el.id || null,
        cls: el.className && el.className.toString ? el.className.toString() : null,
        overflow_x: dx, overflow_y: dy
      });
    }
  });
  document.body.setAttribute('MARKER', JSON.stringify({
    viewport: [document.documentElement.clientWidth, document.documentElement.clientHeight],
    clipped: out
  }));
});
</script>
"""


def find_browser() -> str | None:
    """The first browser this machine has, or None. Ordered, never guessed at."""
    for candidate in _CANDIDATES:
        if candidate.startswith("/"):
            if Path(candidate).exists():
                return candidate
        elif found := shutil.which(candidate):
            return found
    return None


def _page_url(page: Path, browser: str) -> str:
    """*page* as a URL the chosen browser can open, crossing the WSL boundary if needed.

    A Windows browser cannot read a Linux path, so `wslpath -w` supplies the UNC form.
    Its absence is not fatal: a POSIX browser wants the POSIX path anyway.
    """
    if not browser.endswith(".exe"):
        return page.resolve().as_uri()
    win = subprocess.run(  # nosec B603 B607 - fixed argv, path supplied by the caller
        ["wslpath", "-w", str(page.resolve())], capture_output=True, text=True, check=True
    ).stdout.strip()
    return "file://" + win.replace("\\", "\\\\")


def measure(page: Path, browser: str, width: int, height: int) -> dict:
    """The page's overflow report, rendered at *width* x *height*.

    The probe is appended to a copy rather than to *page*: the input is an artifact a
    consumer opens, and a measurement that edits its own subject measures something else.
    """
    probe = _PROBE.replace("TOL", str(TOLERANCE_PX)).replace("MARKER", _MARKER)
    with tempfile.TemporaryDirectory() as work:
        probed = Path(work) / page.name
        probed.write_text(page.read_text(encoding="utf-8") + probe, encoding="utf-8")
        completed = subprocess.run(  # nosec B603 - argv is fixed, no shell
            [
                browser,
                "--headless=new",
                "--disable-gpu",
                f"--window-size={width},{height}",
                "--virtual-time-budget=4000",
                "--dump-dom",
                _page_url(probed, browser),
            ],
            capture_output=True,
            text=True,
            check=False,
        )
    found = re.search(rf'{_MARKER}="([^"]*)"', completed.stdout)
    if found is None:
        raise RuntimeError(
            f"the probe wrote no report: the browser exited {completed.returncode} and its "
            f"output held no {_MARKER}. An unanswered question is not a passing page."
        )
    return json.loads(found.group(1).replace("&quot;", '"'))


def _measure_at_viewport(page: Path, browser: str, width: int, height: int) -> dict:
    """Measure at a *viewport* of the given size, not a window of it.

    A browser window is not its viewport: `--window-size=1920,1080` renders into 1904x985
    here, and measuring the short one reported a 44px clip on a page that has none. The
    trap already cost one lane a false finding, so it is compensated rather than written
    down - the caller asks for the viewport a reader will have.

    One re-run, never a loop: the chrome is a fixed inset, so a second pass lands. A pass
    that still misses reports the viewport it got, and the caller can see the difference.
    """
    report = measure(page, browser, width, height)
    got_w, got_h = report["viewport"]
    if (got_w, got_h) == (width, height):
        return report
    return measure(page, browser, width + (width - got_w), height + (height - got_h))


def main(argv: list[str] | None = None) -> int:
    """Measure one page and report; non-zero when anything is clipped or unanswerable."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("page", type=Path, help="The rendered HTML file to measure")
    parser.add_argument("--width", type=int, default=1920)
    parser.add_argument("--height", type=int, default=1080)
    args = parser.parse_args(argv)

    if not args.page.is_file():
        print(f"render-overflow: no such page: {args.page}", file=sys.stderr)
        return 2
    browser = find_browser()
    if browser is None:
        print(
            "render-overflow: no chrome, chromium or edge found, so nothing was measured. "
            "This fails rather than skips: a skip reads as a pass.",
            file=sys.stderr,
        )
        return 2
    try:
        report = _measure_at_viewport(args.page, browser, args.width, args.height)
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        print(f"render-overflow: {exc}", file=sys.stderr)
        return 2

    clipped = report["clipped"]
    viewport = "x".join(str(n) for n in report["viewport"])
    if not clipped:
        print(f"render-overflow: {args.page.name} at {viewport}: nothing is clipped")
        return 0
    print(
        f"render-overflow: {args.page.name} at {viewport}: {len(clipped)} element(s) hold "
        f"more than they show",
        file=sys.stderr,
    )
    for item in clipped:
        name = item["id"] or item["cls"] or item["tag"]
        print(
            f"  {name}: {item['overflow_x']}px wider, {item['overflow_y']}px taller than its box",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

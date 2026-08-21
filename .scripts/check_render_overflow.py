#!/usr/bin/env python3
"""Report two geometry faults of a rendered page: a box that clips, and boxes that collide.

A green check set cannot see a clip. Three board pages shipped in one day with every
gate passing and a region cut off the screen: a schema dump nobody could read, five
clipped regions - four already clipped on the fixture the layout passed against - and a
panel drawing `0` over ten real edges. Each was found by looking at a screenshot, which
is a human act that does not scale and does not survive a handover.

**A scrollbar is not the signal.** A wall page sets `overflow: hidden`, so it clips in
silence and the absence of a scrollbar proves nothing. The measurement is the DOM's own:
an element whose `scrollWidth`/`scrollHeight` exceeds its `clientWidth`/`clientHeight`
holds more than it shows.

**Overlap is a second fault and it needs a second signal.** The day after this landed it
reported zero on a wall where two gate names were painted over the two below them, and it
was right to: an element drawn across its neighbour does not overflow. Its content fits
its own box - the box is simply in the same place as another box, because a fixed row
height was allotted to a name that took two lines. So the two are measured side by side
and reported apart, one line each, and neither is folded into the other: an overlap
report is a pairwise intersection of the boxes that carry text, an overflow report is an
element measured against itself, and a page can fail either alone.

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

# How the two signals name themselves in the output. Spelled once, because the pass line and
# the failure line of each have to agree, and a reader greps for the prefix.
OVERFLOW = "render-overflow"
OVERLAP = "render-overlap"

_PROBE = """
<script>
window.addEventListener('load', function () {
  // How a finding names the element it found, in the order a reader recognises it.
  var names = function (el) {
    return el.id || (el.className && el.className.toString ? el.className.toString() : '')
      || el.tagName.toLowerCase();
  };
  var says = function (el) { return el.textContent.trim().slice(0, 40); };
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
  // The second signal. Only the elements that *carry text* are paired: a wrapper drawn over
  // a wrapper loses nothing a reader can see, and pairing every element on the page turns a
  // two-row collision into a hundred findings nobody reads.
  var carriers = new Set();
  document.querySelectorAll('*').forEach(function (el) {
    var owns = Array.prototype.some.call(el.childNodes, function (node) {
      return node.nodeType === 3 && node.textContent.trim() !== '';
    });
    if (!owns) { return; }
    var css = getComputedStyle(el);
    // Out of flow is stacked *on purpose*: an overlay, a tooltip and a sticky header are all
    // drawn over their neighbours by design, and a measurement that calls that a defect
    // cannot be left on. Only the normal flow is claimed here.
    if (css.position === 'absolute' || css.position === 'fixed' || css.position === 'sticky') {
      return;
    }
    // Nothing is painted over an element a reader cannot see in the first place.
    if (css.visibility === 'hidden' || css.opacity === '0') { return; }
    var box = el.getBoundingClientRect();
    if (box.width <= 0 || box.height <= 0) { return; }
    carriers.add(el);
  });
  // Only the *outermost* carrier of each run of text. An inline element's rect is its font's
  // em box, not its line box, so a monospace glyph inside a sans line sticks a few pixels out
  // of the block that lays it out and intersects the block above - six such pairs on the
  // board, against one real collision. The ancestor's box holds the inner one anyway, so a
  // real collision is still reported; only the duplicate naming of it is lost.
  var texted = [];
  carriers.forEach(function (el) {
    for (var up = el.parentElement; up; up = up.parentElement) {
      if (carriers.has(up)) { return; }
    }
    texted.push({ el: el, box: el.getBoundingClientRect() });
  });
  var collided = [];
  for (var i = 0; i < texted.length; i++) {
    for (var j = i + 1; j < texted.length; j++) {
      var a = texted[i], b = texted[j];
      // An ancestor's box holds its descendant's by construction, so the pair intersects on
      // every page ever written. `<p>outer <span>inner</span></p>` is not a collision.
      if (a.el.contains(b.el) || b.el.contains(a.el)) { continue; }
      var ix = Math.min(a.box.right, b.box.right) - Math.max(a.box.left, b.box.left);
      var iy = Math.min(a.box.bottom, b.box.bottom) - Math.max(a.box.top, b.box.top);
      // Both axes, and both above the tolerance: two boxes that share an edge intersect in
      // one dimension by zero, and sub-pixel rounding makes that zero a fraction.
      if (ix > TOL && iy > TOL) {
        collided.push({
          a: names(a.el), b: names(b.el), text_a: says(a.el), text_b: says(b.el),
          shared_x: Math.round(ix), shared_y: Math.round(iy)
        });
      }
    }
  }
  document.body.setAttribute('MARKER', JSON.stringify({
    viewport: [document.documentElement.clientWidth, document.documentElement.clientHeight],
    clipped: out,
    collided: collided
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
        return _refuse(f"no such page: {args.page}")
    browser = find_browser()
    if browser is None:
        return _refuse(
            "no chrome, chromium or edge found, so nothing was measured. "
            "This fails rather than skips: a skip reads as a pass."
        )
    try:
        report = _measure_at_viewport(args.page, browser, args.width, args.height)
    except (
        RuntimeError,
        subprocess.CalledProcessError,
        json.JSONDecodeError,
        UnicodeDecodeError,
    ) as exc:
        return _refuse(str(exc))

    where = f"{args.page.name} at {'x'.join(str(n) for n in report['viewport'])}"
    # Both signals report on every run and the exit code is their disjunction. Neither
    # short-circuits the other: a page that clips nothing may still paint a row over a row,
    # which is the pair of runs that produced this second signal in the first place.
    clipped = _report_clipped(report["clipped"], where)
    return max(clipped, _report_collided(report["collided"], where))


def _refuse(reason: str) -> int:
    """Exit 2 under *both* prefixes: a reader grepping one must not read the silence as a pass."""
    print(f"{OVERFLOW}/{OVERLAP}: {reason}", file=sys.stderr)
    return 2


def _report_clipped(clipped: list[dict], where: str) -> int:
    """The overflow signal: elements holding more than their own box shows."""
    if not clipped:
        print(f"{OVERFLOW}: {where}: nothing is clipped")
        return 0
    print(
        f"{OVERFLOW}: {where}: {len(clipped)} element(s) hold more than they show",
        file=sys.stderr,
    )
    for item in clipped:
        name = item["id"] or item["cls"] or item["tag"]
        print(
            f"  {name}: {item['overflow_x']}px wider, {item['overflow_y']}px taller than its box",
            file=sys.stderr,
        )
    return 1


def _report_collided(collided: list[dict], where: str) -> int:
    """The overlap signal: pairs of text-carrying boxes drawn over one another.

    The text each box holds is printed beside its class, because a grid of cells that all
    share one class is named identically by every other field and `noqa-debt` under
    `projection-permissions` is the whole finding.
    """
    if not collided:
        print(f"{OVERLAP}: {where}: nothing is drawn over anything")
        return 0
    print(f"{OVERLAP}: {where}: {len(collided)} pair(s) of text boxes intersect", file=sys.stderr)
    for item in collided:
        print(
            f"  {item['a']} {item['text_a']!r} over {item['b']} {item['text_b']!r}: "
            f"{item['shared_x']}x{item['shared_y']}px shared",
            file=sys.stderr,
        )
    return 1


if __name__ == "__main__":
    raise SystemExit(main())

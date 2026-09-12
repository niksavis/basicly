#!/usr/bin/env python3


from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess  # nosec B404 - a browser is the only instrument that can answer this
import sys
import tempfile
from pathlib import Path

_CANDIDATES = (
    "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe",
    "/mnt/c/Program Files (x86)/Microsoft/Edge/Application/msedge.exe",
    "google-chrome",
    "chromium",
    "chromium-browser",
)

TOLERANCE_PX = 2

_MARKER = "data-overflow-report"

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
    for candidate in _CANDIDATES:
        if candidate.startswith("/"):
            if Path(candidate).exists():
                return candidate
        elif found := shutil.which(candidate):
            return found
    return None


def _page_url(page: Path, browser: str) -> str:

    if not browser.endswith(".exe"):
        return page.resolve().as_uri()
    win = subprocess.run(  # nosec B603 B607 - fixed argv, path supplied by the caller
        ["wslpath", "-w", str(page.resolve())], capture_output=True, text=True, check=True
    ).stdout.strip()
    return "file://" + win.replace("\\", "\\\\")


def measure(page: Path, browser: str, width: int, height: int) -> dict:

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

    report = measure(page, browser, width, height)
    got_w, got_h = report["viewport"]
    if (got_w, got_h) == (width, height):
        return report
    return measure(page, browser, width + (width - got_w), height + (height - got_h))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Report two geometry faults of a rendered page: clipping and overlap."
    )
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
    clipped = _report_clipped(report["clipped"], where)
    return max(clipped, _report_collided(report["collided"], where))


def _refuse(reason: str) -> int:
    print(f"{OVERFLOW}/{OVERLAP}: {reason}", file=sys.stderr)
    return 2


def _report_clipped(clipped: list[dict], where: str) -> int:
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

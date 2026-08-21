// Draw mermaid blocks with the same library version the hosting surface runs.
//
// Reads {"blocks":[{"id":..,"text":..}],"mode":"render"|"parse"} on stdin and writes
// {"version":..,"results":[{"id":..,"error":..|null}]} on stdout. `check_mermaid.py`
// owns discovery and reporting; this file owns the one thing only node can do.
//
// `mode` defaults to `render` and the check never sends anything else. It exists
// because `tests/test_check_mermaid.py` asks for `parse` on the same three blocks it
// asks for `render`, which is the two-directional control behind the criterion: drop
// it and "render is stronger than parse" is an assertion in a comment.
//
// jsdom stands in for the browser, and every text measurement is shimmed because
// jsdom implements no layout at all. The measurements are approximations, so the
// geometry of the output means nothing -- but `mermaid.render` still detects the
// diagram type, parses the text and runs the diagram's own `draw`, which is where
// every defect this gate exists for is thrown.
//
// The shims return a size proportional to the text rather than zero, and that is
// load-bearing: with zero-sized boxes the layout maths degenerates and refuses
// valid diagrams -- a `classDiagram` carrying a note failed with "svg element not
// in render tree" until these returned something.

import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

import { JSDOM } from "jsdom";

const CHAR_WIDTH = 8;
const LINE_HEIGHT = 18;

// jsdom exposes these but node also defines its own, and node's must win: copying an
// intrinsic or a timer across realms breaks `instanceof` and dies when the window closes.
const FORCED = ["window", "document", "getComputedStyle", "requestAnimationFrame"];

function textBox() {
  const lines = (this.textContent || "").split("\n");
  const widest = Math.max(1, ...lines.map((line) => line.length));
  return { x: 0, y: 0, width: widest * CHAR_WIDTH, height: lines.length * LINE_HEIGHT };
}

function measuringContext() {
  return {
    font: "",
    measureText: (text) => ({ width: String(text).length * CHAR_WIDTH }),
  };
}

function define(name, value) {
  Object.defineProperty(globalThis, name, { value, configurable: true, writable: true });
}

function installDom() {
  // `url` is not decoration: with an opaque origin, reading `localStorage` -- which
  // jsdom exposes and mermaid's config touches -- throws before any diagram is seen.
  const { window } = new JSDOM("<!DOCTYPE html><body></body>", {
    pretendToBeVisual: true,
    url: "https://localhost/",
  });
  window.SVGElement.prototype.getBBox = textBox;
  window.SVGElement.prototype.getComputedTextLength = function computed() {
    return (this.textContent || "").length * CHAR_WIDTH;
  };
  window.SVGElement.prototype.getScreenCTM = () => null;
  // Only `mindmap` reaches for a canvas, and only to measure text; jsdom has none
  // without a native build, so it refused a valid mindmap with "Could not create
  // canvas of type 2d" rather than reporting anything about the diagram.
  window.HTMLCanvasElement.prototype.getContext = measuringContext;
  for (const name of Object.getOwnPropertyNames(window)) {
    if (!(name in globalThis)) define(name, window[name]);
  }
  for (const name of FORCED) define(name, name === "window" ? window : window[name]);
}

// Read from the package node resolved, not from the repository's own package.json, so the
// number the report prints is the number that did the drawing. Resolved through
// `createRequire` rather than a JSON import: `import ... with { type: "json" }` is newer
// than the node the CI matrix pins, and a syntax error there fails on three runners only.
function libraryVersion() {
  const require = createRequire(import.meta.url);
  const manifest = require.resolve("mermaid/package.json");
  return JSON.parse(readFileSync(manifest, "utf8")).version;
}

async function main() {
  const chunks = [];
  for await (const chunk of process.stdin) chunks.push(chunk);
  const { blocks, mode } = JSON.parse(Buffer.concat(chunks).toString("utf8"));

  installDom();
  const mermaid = (await import("mermaid")).default;
  mermaid.initialize({ startOnLoad: false, securityLevel: "loose" });
  const draw =
    mode === "parse"
      ? (block) => mermaid.parse(block.text)
      : (block) => mermaid.render(`check-${block.id}`, block.text);

  const results = [];
  for (const block of blocks) {
    try {
      await draw(block);
      results.push({ id: block.id, error: null });
    } catch (err) {
      results.push({ id: block.id, error: String(err && err.message ? err.message : err) });
    }
  }
  process.stdout.write(JSON.stringify({ version: libraryVersion(), results }));
}

main().catch((err) => {
  process.stderr.write(`render_mermaid: ${err && err.stack ? err.stack : err}\n`);
  process.exit(3);
});

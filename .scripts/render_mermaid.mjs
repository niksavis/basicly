
import { createRequire } from "node:module";
import { readFileSync } from "node:fs";

import { JSDOM } from "jsdom";

const CHAR_WIDTH = 8;
const LINE_HEIGHT = 18;

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
  const { window } = new JSDOM("<!DOCTYPE html><body></body>", {
    pretendToBeVisual: true,
    url: "https://localhost/",
  });
  window.SVGElement.prototype.getBBox = textBox;
  window.SVGElement.prototype.getComputedTextLength = function computed() {
    return (this.textContent || "").length * CHAR_WIDTH;
  };
  window.SVGElement.prototype.getScreenCTM = () => null;
  window.HTMLCanvasElement.prototype.getContext = measuringContext;
  for (const name of Object.getOwnPropertyNames(window)) {
    if (!(name in globalThis)) define(name, window[name]);
  }
  for (const name of FORCED) define(name, name === "window" ? window : window[name]);
}

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

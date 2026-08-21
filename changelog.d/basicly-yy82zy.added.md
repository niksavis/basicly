- **A `mermaid` verify check draws every committed diagram with the renderer the reader's
  browser runs.** The architecture document carries 16 mermaid blocks and the README one, and
  nothing looked at any of them: a block with an error renders as a red box on the hosting site
  while every gate here stayed green. One revision named a `sequenceDiagram` participant `Loop`,
  which collides with mermaid's `loop` keyword — a parser caught that one, review did not.

  **The criterion is renders, not parses, and that distinction is measured rather than assumed.**
  `mermaid.parse` stops at the grammar and never runs the diagram's own `draw`. Three blocks
  parse clean and refuse to render on this version: a subgraph whose id repeats a node id, a
  `gantt` task with an unparseable date, and a `stateDiagram-v2` note on a state that does not
  exist. A check written to `parse` would have passed all three, and the tests run those three
  through both instruments so the claim stays a measurement.

  **The renderer is pinned to what the hosting surface actually serves.** GitHub Pages publishes
  `site/`, which holds no markdown, so it renders none of these blocks; the surface a reader sees
  is github.com's own markdown view, which draws mermaid from
  `viewscreen.githubusercontent.com/markdown/mermaid`. That bundle runs mermaid 11.16.1, so
  `package.json` pins 11.16.1, the check prints both numbers on every run, and a drift between
  them fails rather than being logged — a check pinned to the wrong renderer is a gate that
  agrees with itself. Nothing skips: a missing node, a missing `npm install`, a renderer that
  writes no usable report and a tree holding zero blocks all exit non-zero, because a skip and a
  pass are the same line in a log and an empty population is the collector breaking.

  The cost is a dependency addition an owner approved: `mermaid` and `jsdom`, 147 packages, no
  browser download — `@mermaid-js/mermaid-cli` was rejected for its `puppeteer` peer dependency
  (basicly-yy82zy).

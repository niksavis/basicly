- **A wall row now names the feature it implements.** The operator's report was that
  `P1  basicly-a4q3.10  Carry a ranked kill list and its discriminator on a change summary`
  carries a priority, an id and a title, and nothing saying which feature it serves. The
  fact was already on the wire, so the ready set is now grouped under the epic or feature
  each row resolves to and no producer field was added: the parent edges are the `graph`
  section and the titles are the `units` section, both folded from the document the tick
  already carries.

  Rows group under the **root** ancestor rather than the immediate parent, because the epic
  is the name a reader recognises. Verified against the live document and not only against
  the fixture, whose graph carries `blocks` edges and no `parent-child` edge at all: every
  heading on the page is the title of that row's root in `graph.edges`, with
  `basicly-0hxck3` resolving two levels up to `basicly-k6tpep`.

  A heading counts the **whole ready set**, not the rows drawn beneath it. The unattached
  heading reads 41 while six of its rows are drawn, and 41 is the orphan count derived
  independently over the same snapshot — a quarter of the ready set attached to no feature
  at all, which is the second finding the grouping makes visible and which a slice-derived
  count would have hidden.

  Two defects were found by exercising the change rather than by reading it. A unit that
  merely *feeds* a cycle took the title of whichever member the walk halted on, filing it
  under a feature the graph never claimed; the regression test was run against the pre-fix
  walk to confirm that only that case discriminates, the two obvious cycle shapes passing
  either way. And a heading is a drawn line, so it now spends a slot: six headings over
  fourteen rows ran the ready region 137px past its box at 1440x900, which the same document
  rendered through the previous template does not do.

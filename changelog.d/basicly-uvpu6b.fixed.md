- **A long gate name no longer paints over the check below it, and the measurement that missed
  it now exists.** The footer's gate strip reserves a grid of one-line rows, but only the
  *cell* was held to one line - the name inside it was free to wrap, and did:
  `projection-permissions` and `declared-dependencies` each took a second line the row height
  had already allotted to `noqa-debt` and `ledger-bodies` and were drawn across them. Measured
  on this repository's own snapshot at six widths, the collision is present from 1200px to
  1800px and absent at 1920px, which is why the layout passed review. Six columns hold the
  tree's longest check name only at 1920px and no column count holds it at every width the
  layout claims, so the name now declares its truncation the way every other text on the page
  does rather than wrapping.

  **The instrument is the more durable half.** `.scripts/check_render_overflow.py` reported
  zero on the broken wall and was *right* to: an element painted across its neighbour does not
  overflow - its content fits its own box, the box is simply in the same place as another box.
  Overlap and overflow are different faults, so the script now measures both in one browser
  pass and reports them as two independent signals under two prefixes, `render-overflow` and
  `render-overlap`, with the exit code their disjunction and a refusal printed under both. The
  overlap signal is a pairwise bounding-box intersection over the *outermost* elements that
  carry their own text; that qualifier is a false-positive class, not a detail - an inline
  box is its font's em box rather than its line box, so a monospace glyph inside a sans line
  produced six spurious pairs on the live board against one real collision. Elements out of
  normal flow, invisible elements, and an ancestor holding its own descendant are excluded for
  the same reason.

  Two committed fixtures prove the two signals discriminate, each being the positive control
  for one and the negative control for the other: `tests/fixtures/render/clipped-and-not.html`
  reports 1 clip and 0 collisions, and the new
  `tests/fixtures/render/overlapping-and-not.html` reports 0 clips and 1 collision, alongside
  three quiet controls - boxes that merely touch, a parent carrying text around a child
  carrying text, and an absolutely positioned overlay. Both signals now report nothing on the
  live board at 1920x1080 and at 1200x900, where before the fix the overlap signal named both
  reported pairs and the clip signal named neither. A test binds the render fixture's longest
  gate name to this repository's own `[[verify.checks]]`, so a longer check name landing fails
  in the suite rather than on the wall (`basicly-uvpu6b`).

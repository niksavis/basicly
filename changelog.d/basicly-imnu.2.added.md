- **A consumer now has a written path from `basicly install` to a first shipped bead.**
  `docs/` carried a reference (the architecture file) and explanation (design and research),
  and nothing else — a repo could install the harness, read the whole architecture, and still
  not know which command comes after `install`. The new layer closes the two missing Diátaxis
  quadrants: a tutorial (`docs/tutorial/first-loop.md`) that walks a scratch repo from install
  through filing a bead, the classify checkpoint, building in the provisioned worktree, the
  landing and the ship approval; and six task-focused how-tos (`docs/how-to/`) for the
  recurring operations — customizing the catalog through the overlay, wiring the verify gate
  (which passes *vacuously* until you declare checks), unblocking a commit a hook refused,
  upgrading and detecting drift, running parallel lanes, and resuming or handing over a track.
  Every command and quoted output in the tutorial was executed against a fresh repo before it
  was written, which is how it came to document the two gates that refuse a fresh install's own
  first commit: `catalog-lint` demanding a `[catalog] rank1_floor`, and the missing beads issue
  id. README and architecture §13.1 point at the layer; §15's roadmap row moves to `shipped`
  (basicly-imnu.2).

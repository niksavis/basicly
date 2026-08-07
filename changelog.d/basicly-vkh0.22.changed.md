- **`br` is still required. This release owns the work *store*, not yet the floor.**
  The kit's append-only event log is complete and checkable — provenance on every
  edge, collision-budgeted ids, a derived snapshot with rotation and a staleness
  header, `fsck` and `rebuild`, import with tombstones, and a shadow differential
  that refuses a comparison against a re-import of its own export. `[tracker] mode`
  puts a repo on a rung of the cutover.

  What it does **not** do is remove the `br` binary from what a consumer needs
  installed, and an earlier draft of the roadmap said it did. `owned` flips
  `br.read_record` — one seam — while 44 further spawn sites remain, 26 of them
  `comments`, which is the carrier for every checkpoint, gate marker, grant and
  rework record. Measured with no `br` on `PATH` and the flip forced on:
  `policy.gate_status` and `policy.definition_of_ready` both still raise
  `br is not on PATH; the harness requires the beads tracker`.

  So install `br` as before. The claim that you will not need to is carried to
  `v1.0.0`, whose acceptance test drives a fresh consumer repo with no `br`
  through one unit of work to a landed commit — a test that can fail, rather than
  a sentence in release notes that cannot (`basicly-vkh0.22`).

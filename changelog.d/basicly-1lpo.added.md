- **`basicly loop supervise --label LABEL` fans a pass out over the beads carrying a label,
  instead of the root's parent-child children.** A release cut is assembled from work that
  already exists, and `br` permits exactly one parent, so every bead in a cut already has an
  epic of origin. Parent-child fan-out therefore could not express a release at all: the root
  could gate the work as `blocks` dependencies — enough for the autonomy grant, which walks
  both edge kinds — and still seed none of it, because seeding walks descent only. A cut drawn
  from existing epics could not be one pass.

  With `--label`, membership is a tracker query rather than a graph edge. The root keeps the
  three jobs it is genuinely good for — anchoring the grant, the singleton lock and the
  decision queue — and stops being the thing that decides which beads are in. Nothing is
  re-parented, so a bead's epic of origin survives being included in a cut, and the same bead
  can appear in a later cut under a different label.

  This is the selector `[policy] phase-*` labels and `br list --label` already implied: phase
  membership has been a label since the plan stopped listing bead ids, and this makes the
  supervisor read membership the same way (`basicly-1lpo`).

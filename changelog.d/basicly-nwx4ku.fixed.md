- **A ratchet fragment can record the commit its measurements were taken at, and is refused
  where that commit is not in the head's history.** A `basicly.d` delta composes in any order,
  which is what the directory is for; the *headroom* a lane measures before choosing that delta
  does not. Two lanes branched from one commit both measured `src/basicly/merge.py` at exactly
  2 tokens of module-size headroom, each declared a rebaseline that fitted and spent that same
  2, and the composed tree came out 2 over — green on both branches, red only on the merge, and
  the operator saw a two-token overrun with nothing pointing at two independently correct
  measurements.

  `[ratchet] base_commit` is the commit a fragment's numbers were measured on, and
  `dropin.compose` refuses the fragment when `HEAD` does not contain it, naming the fragment,
  the gate and the sha. **Ancestry, not equality:** work landing on top of a measurement does
  not stale it, so a fragment that has been rebased forward still applies. **Absence is not a
  violation:** the field is hand-written, nothing in the tree writes a fragment to derive it
  at, and every fragment that predates it composes exactly as before — the alternative would
  have stopped every lane in flight. Nor is git's third answer a violation: where the head's
  history is not there to read, a tree copied without its `.git` or a shallow clone, the check
  has nothing to say (basicly-nwx4ku).

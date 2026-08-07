- **A bead is read through one seam with one absence contract.** `basicly.br` was already
  the only place that *spawns* `br`, but not the only place that *reads* it: the
  single-record unwrap was written out at **eleven call sites across eight modules**, and
  they disagreed about failure four ways — two raised, two returned `None`, four returned a
  local empty, one carried a typed absence. A twelfth site guarded the payload shape not at
  all and would have raised `AttributeError` on a non-object payload.

  Now there are two functions and one rule. `br.read_record` returns the record or `None`
  for every way a read comes back without one — `br` absent, a spawn that raises, a non-zero
  exit, output that is not JSON, an empty array, a payload that is not an object.
  `br.require_record` is the hard half and raises **one** message naming the bead, so a
  caller no longer has to know whether it is looking at a missing bead, a missing binary or
  a malformed payload to say what went wrong.

  A tree guard fails the build if any module outside `basicly.br` writes the unwrap again —
  the same reason one reader exists for both of `br`'s dependency spellings. It matches the
  unwrap *expression* rather than any list check, because a plain shape guard on JSON is not
  the defect.

  This is what made the tracker cutover a change to one function instead of eleven
  decisions: the replacement chooses what "not found" means, and an empty list is the
  natural in-process answer — which against the old eleven would have split six sites from
  five, at runtime, across eight modules (`basicly-tcmy.14`).

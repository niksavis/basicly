- **A rework loop that is not converging now stops on the finding set, not on the
  attempt count.** Every gate that reports findings — a verify run's failed checks, a
  lane's failed rubric checks, a landing's own report — records them on the bead as a
  canonical member list, and the next round is compared against the previous one
  rather than merely counted. The count was never the measure: an attempt that
  re-derives the previous attempt's verdict verbatim was charged in full, and at
  `[policy] max_rework = 2` a node could reach a human having spent its whole budget
  re-reporting a finding set already on its own bead.

  One repeated round **warns**, on the bead and in the escalation the cap raises, so
  whoever triages it can see that a re-dispatch would learn nothing — a gate only
  reports what it checks, so one repeat may still hide a real change. Two consecutive
  repeated rounds **escalate immediately**, and the attempt is refunded so the
  remaining cap survives for whatever the human answers. A finding set that *grew*
  escalates on its first occurrence: the previous findings are all still open and new
  ones joined them. The refund is spendable once per bead and gate, so a node nobody
  answers still reaches its cap instead of being forgiven forever.

  The signature history and the comparison now live in one place next to the rework
  counter that owns this accounting, and the merge gate's repeat-bounce check
  (`basicly-bdd4`) delegates to it. Only the threshold stays per gate: a repeated
  landing conflict escalates on the first repeat, because re-applying one branch to
  one anchor provably cannot converge (`basicly-m4zv.5`).

- **A landed unit's cost record carries the forecast it was priced against, and counts the
  curator's dispatch.** Two defects in the `[harness-cost]` marker, measured over all 202 cost
  records in this repository's ledger on 2026-08-17. `forecast.tokens` was null in 185 of them
  and `scope_tokens` was null with it, so forecast against actual could not be computed for
  that population at all: the rollup looked the frozen estimate up by the record's *ownership
  scope* while the estimate had been priced over its *working set*, and a different glob set is
  a different key, so the lookup missed and returned a null rather than a forecast. The rollup
  now resolves through the same dispatch sizing the lane was priced by and falls back to the
  frozen forecast; when neither answers it records **the reason the forecast is absent** in a
  new `source` field instead of a bare null, and labels a forecast it computed itself `rollup`
  rather than borrowing the dispatch label, because an unfrozen resolution prices with today's
  factors and this runs after the merge.

  Second, the curator's dispatch was never in the total for the units that had one:
  `loop._on_ship` wrote the rollup and *then* dispatched curation, so the rollup preceded the
  spend it was meant to count. 8 of 202 units disagreed with the run records they hold and
  none over-counted — the worst reported one dispatch against three runs, 10.1% below two
  independent instruments that agreed on the real figure. The two calls are swapped. The
  rollup still precedes the tracker-state commit, because a marker written after it sits in
  the local store only, and that is the constraint the ordering had to keep (basicly-agzx.4).

- **A unit parked in `validate` is advanced by a supervised pass instead of only counted by
  the WIP bound.** `wip.DOWNSTREAM_PHASES` has counted `verify`, `validate` and `ship` since
  the VALIDATE phase landed, while `supervise.advance_parked` drove `verify` and `ship` only.
  A unit whose derived phase was `validate` was therefore charged against
  `[policy] max_downstream_wip` and advanced by nothing, so five of them refused every further
  dispatch and the queued decision told the operator to land lanes the pass could not land.

  `advance_parked` now drives `wip.DOWNSTREAM_PHASES` itself — one definition, imported rather
  than respelled — and both supervised drives (`advance_parked` and the post-ship drive in
  `_land_green`) name the session as their grant root, so the validator those drives can now
  spawn is refused by D3's spend halt before it starts rather than running unmetered inside a
  landing pass (`basicly-xab3`).

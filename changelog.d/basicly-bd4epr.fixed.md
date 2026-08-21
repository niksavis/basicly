- **The board no longer goes backwards the moment a supervisor starts, and `IN FLIGHT` finally
  has a producer.** A live supervisor lock hands board production from `basicly board serve` to
  the supervisor's own heartbeat - the server stops folding and serves the supervisor's file
  instead - and the tick folded on the lock alone. Measured on this repository: with no
  supervisor the board carried a phase on 234 of 234 units, a ready set and `backlog.ready` /
  `backlog.blocked`; a supervised pass reverted every one of those to *not emitted by this
  producer*, and `IN FLIGHT` had never had a producer on that path at all.

  The tick now folds the same document `basicly board --out` folds, plus the in-flight lanes.
  The phase per record comes from `loop_state.phase_map` - one fold of the log for the whole
  population, measured at 84 ms over 1041 records, against the per-record read that priced the
  section out when the old reasoning was written - and each lane card reuses the view
  `loop session` already builds, so there is one answer to what a lane last ran rather than two.
  The lane selector the pass was started with rides along, so a `--label` pass draws its own
  lanes and not the root's children.

  A fact the tick genuinely cannot gather still leaves its section **absent** rather than
  zeroed: with no lock the `session` and `lanes` sections are omitted, and a session this
  checkout cannot derive publishes no lane list at all. A failed emission still costs one
  narrative line and never the pass.

  One emission measures 1.50 s on this repository, and 7.11 s - 47% of the 15 s beat - once
  run records exist, because the grant-spend walk behind `session.spent_tokens` costs 5.9 s of
  that. It runs after the heartbeat write, so it delays the next beat and never a landing, and
  clears the 60 s staleness horizon by 8x (`basicly-bd4epr`).

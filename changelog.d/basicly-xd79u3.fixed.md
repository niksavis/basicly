- **A validator whose reply carried no verdict no longer leaves the loop in exactly the
  state it was in.** The `validate` advance dispatches a validator against the merged
  change and reads its `VALIDATION: PASS`/`FAIL` line off the reply; when there is no such
  line there is no verdict to record, and the advance used to return having written nothing
  anywhere — no gate event, no queue item, no rework — so `loop status` reported the same
  gate and `loop decisions` the same empty queue as before the dispatch, and the only
  surface that showed the run at all was the spend. Measured on `basicly-gvlpxm`: one
  advance dispatched a validator and two reviewers, all exiting 0, one of them alone
  charging $1.13, and the ledger's gate-event count for the record did not move. The reply
  is not stored and the run record carries usage rather than text, so nothing after the
  fact could recover what the validator had said. It is now queued as a `validate` decision
  carrying that reply, and the advance blocks on it — an unreadable verdict is a fact an
  operator can dispose of, where silence is not. A verdict that *is* readable still records
  the gate exactly as before, and an advance that dispatched no validator still records
  nothing: a fix that wrote a gate event unconditionally would have turned a fail-silent
  into a fail-open. The refusal a recorded `FAIL` prints also stops claiming that no result
  was recorded, which was the same confusion in a second place (basicly-xd79u3).

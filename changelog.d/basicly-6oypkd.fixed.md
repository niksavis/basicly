- **A tracker write naming a record the ledger does not hold is refused, naming the id, instead
  of reported as recorded.** `basicly tracker write -- update <typo>` printed
  `recorded: update <typo>` and exited zero, on the one surface an operator uses to check their
  own work. Measured 2026-08-20 against a seeded ledger, the cost was worse than the report
  said. Only the flagless form wrote nothing at all; `update <typo> -t bug` **landed**, and the
  fold turned the mistyped id into a record no `create` ever minted, carrying whichever
  half-fact the argv stated. All five write verbs that reach the owned append — `close`, `comments add`,
  `dep add`, `gate report` and `update` — accepted an absent id and appended an event for it;
  `dep remove` was the only one that refused, because `basicly-he6200` had made it check the
  edge it was withdrawing. The append now reads the record set under the lock it is about to
  write through and refuses the whole batch, quoting the argv and the id it could not find.
  `create` is untouched and stays the exception: it mints its id in the same critical section
  and never comes through the append at all.

  Idempotence is unaffected, which is what makes this refusable at the seam rather than at each
  caller: a record's existence only ever moves one way, since a delete leaves a tombstone and
  the record stays in the fold, so no engine path that re-enters a state on every advance can
  meet the refusal on a later pass having got past it on the first. An edge's *target* is still
  unchecked — a dangling target is a different claim, and `merge` and `supervise` both add edges
  best-effort. One fixture relied on the old tolerance: `tests/test_gate_source.py` reported
  gates through the real seam against a record nothing had opened, and now opens it
  (basicly-6oypkd).

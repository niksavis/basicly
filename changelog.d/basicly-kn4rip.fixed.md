- **A write the ledger already held no longer reports `recorded:`.** An event id is a digest
  over the fact, so a fact the ledger already holds is skipped as an idempotent replay - but
  `basicly tracker write` printed `recorded:` from no exception having been raised rather than
  from what landed, so the skip read as success. `--add-label live-demo`, then
  `--remove-label live-demo`, then the same add again confirmed a label write three times over
  a field that never moved, and the third confirmation is what bought a wrong diagnosis. The
  seam now says `already recorded, so nothing was appended` and adds that the record still
  reads as it did.

  The swallow itself is deliberately unchanged, so a genuine duplicate replay still appends
  once and states nothing new. **Saying a re-record is meant is still not possible:** driving
  one field to A, to B, and back to A leaves it at B, because the history `A, B` and a
  deliberate re-record of `A` after `B` leave the ledger byte-identical and no rule reading it
  can separate them. The intent has to come from the caller, which is what `Draft.generation`
  is for and what no write verb yet reaches (`basicly-z9bggw`) (basicly-kn4rip).

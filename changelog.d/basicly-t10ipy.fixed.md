- **The tracker kit's `fsck` now names a hole in a record's sequence chain, and it runs in the
  verify set instead of only when somebody thinks to run it by hand.** `fsck` over this
  repository's ledger reported one `broken` finding — `carried-totals` on a comment event of
  `basicly-vkh0.30` — and that was the consequence, not the defect. The record holds sequences
  1-8 and 10-34: **no event claims sequence 9**, so 33 events sit under a highest sequence of
  34. The fold is right that 33 events are there; the carried total of 10 on the event at
  sequence 10 is a faithful record of a writer that read a max of 9, so it was already wrong
  when it was written. §4.1 has the writer assign max+1 and the log has one append path, which
  makes the chain contiguous by construction — a hole means a line that was written is gone.

  `fsck` had `forked-sequence` for two events claiming one number and nothing for a number no
  event claims, so it reported the disagreement instead of the cause. §4.6 already voids a
  *forked* item's carried totals so one root defect does not print as a page of findings; a gap
  voids them for the same reason and was not handled. Measured on a seeded ledger: the old
  checker printed **two** `carried-totals` findings for **one** missing event and never
  mentioned the gap. It now prints one `sequence-gap` naming the missing number, and carries no
  event ids — the events either side are sound, and pointing a reader at them is the wrong
  report.

  The event itself was not repaired and cannot be: an append-only log has no undelete, and no
  commit in this repository's history ever contained a sequence 9 line for that record. So the
  new `ledger-fsck` check declares it in `[tool.ledger_fsck.frozen]`, keyed `<record>/<kind>`
  so an allowance for one defect cannot absorb a different one landing on the same record, and
  binds on everything else — a new finding, a recorded one that grew, or a recorded one that
  fell and was not banked. It costs 0.18s over 6,157 events and 1,005 records, so it runs in
  both modes (basicly-t10ipy).

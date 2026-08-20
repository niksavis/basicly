- **The tracker kit's provenance fold now reads the edge dialect the engine actually writes,
  so `gating_edges` can see the population instead of answering for it with an empty set.**
  `provenance.fold_edges` required an edge payload spelled `target`/`edge_type` while
  `migrate.py` writes `from`/`to`/`type` and `differential.py` reads that spelling. Measured
  on this repo's own ledger: of 1,083 committed `edge` events, the fold read **0** and filed
  all 1,083 under `EdgeFold.malformed`, which nothing reads — so `gating_edges` returned an
  empty tuple, and empty is also the correct answer for a ledger with no edges at all. That
  is the fail-open shape, and it survived only because nothing in `src/` or `.scripts/` had
  wired the fold yet; a reader who wired it later would have inherited a silent zero.

  The engine's pair is now accepted on **read** and still never written, taken off
  `migrate.py`'s own constants rather than respelled, and chosen only when it is complete and
  the declared pair is not — so a payload in neither dialect is still refused, naming the
  documented spelling instead of guessing which writer produced it. `EdgeFold.dialects`
  reports how many events were read in each spelling, which is what makes an empty edge set
  distinguishable from an unreadable one. On the same ledger the fold now reads 1,065 edges
  with 0 malformed against `differential.views_from_events`'s 1,064 — they differ by the one
  retracted edge, which that fold models and this one deliberately does not. Unifying the
  spellings instead was rejected: `provenance.KEY_TARGET` is read by `fsck.EDGE_RECORD_KEYS`,
  so moving it is a writer change reaching two modules this fix does not own (basicly-svct4w).

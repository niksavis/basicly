- **The tracker design's speed argument quoted a p95 as a typical call, overstating the
  advantage of owning the tracker by ~90×.** It cited 113 ms for one `br` CLI read and
  concluded an in-process read was "~175× cheaper". Both numbers were wrong.

  Re-measured from this repo's own committed call ledger — 1,420 recorded engine calls to
  `br` — the median is **14.2 ms** and 113 ms is approximately the **p95**. And the 175×
  ratio compared that p95 against a *single-record* read of a ledger a third of today's
  size: the slow end of one distribution against the fast end of another.

  Held to one comparison at a time, against the live 2.30 MB / 642-record ledger: a full
  fold is **~1.9×** cheaper than the median call, and a single-record read **~15×**. A fold
  is O(events) while a spawn is roughly constant, so the fold ratio *narrows* as the ledger
  grows unless the carried aggregate keeps the common query off the fold.

  This mattered because speed is one of the stated arguments for owning the tracker, and a
  175× claim justifies a release where a 1.9× claim does not. The arguments that do carry it
  are untouched: ownership of the harness's own state, the licence rider restricting a class
  of users, and twelve paid-for defects carried as requirements. The correction removes a bad
  reason for a good decision. The same figure was stale in `architecture.md`, which the bead
  had not named and which outlives the design document (`basicly-rxc1`).

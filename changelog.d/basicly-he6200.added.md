- **A dependency edge can be retracted, so a decision that inverts one can be enacted.** The
  work tracker could add an edge and never remove one, which left an owner decision that
  *reverses* which of two records goes first with no safe route: adding the reverse edge without
  withdrawing the original closes a two-record cycle, and the cycle report cannot be relied on to
  refuse it. `basicly tracker write -- dep remove <record> <target> -t <type>` now records a
  retraction. It is a retraction and not a deletion — the ledger stays append-only, the fold
  answers with the edge gone, and the history still reads as asserted then withdrawn, which is
  the shape a tombstoned record already had. Two decisions are made explicitly: retracting an
  edge the ledger does not hold is **refused**, naming both records, because a typo in a record
  id would otherwise record a withdrawal of nothing while reading as success; and a
  `parent-child` edge is **not retractable**, because removing one re-parents a record and
  `basicly loop supervise` fans out over `parent-child` dependents, so it would silently change
  which records a supervised run touches (basicly-he6200).

- **A handoff artifact kind with no producer is now reported as unwired instead of counting as a
  live contract.** Eight kinds are named and seven have a schema, which read as seven contracts;
  three run. The four schemas nothing records — `classification`, `change-shape`,
  `verification-evidence` and `validation-transcript` — resolved through the same seam the wired
  three do, so `handoff.adopted` answered yes for a kind no state produces and no state reads, and
  `handoff.record` would refuse a payload for an artifact that never travels.

  `handoff.PRODUCERS` declares, per kind, the `module:function` that records it or `None`, and
  `handoff.wired` is the predicate `_validator` consults before it resolves a schema file. So an
  unwired kind is inert at both ends, the way an uninstalled schema already was.

  **Declared, not derived from absence.** Searching for a caller cannot tell "unwired" from
  "probed wrongly": a search for a kind's own name returns the English word, six files for
  `classification` and five of them prose. A missing declaration is not ambiguous. Two states and
  no third — *why* an unwired kind has no producer is a backlog fact, and a copy of it here would
  go stale the day a record lands.

  The declaration is kept honest by its own test: each declared producer is read out of the
  package's source and shown to define the named function, to name that kind's own constant, and to
  be called. A renamed or dead producer therefore fails as a defect rather than demoting its kind
  to unwired, which would hand the fail-open answer straight back to absence.

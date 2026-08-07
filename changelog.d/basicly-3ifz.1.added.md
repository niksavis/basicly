- **`basicly usage tuning` advises every governed factory parameter from the outcomes it
  actually produced.** Almost every number governing the factory was set by judgment and then
  never revisited; this is the readable half of the feedback loop the exceptions already had.
  It reads the dispatch ledger from **both** corpora — the self-ignored local run records and
  the committed `[harness-run]` markers, deduplicated so a dispatch in both is one sample —
  and names which corpus each sample came from. Per parameter it reports the value in force
  for the dispatches it summarises (a session override puts its dispatches in their own
  cohort with their own outcome distribution) and a recommendation with its sample size:
  `measured` at or above `[policy.sizing] calibration_min_samples`, otherwise `seeded`, where
  the declared prior stands and the row names the in-force value it would displace — never a
  number fitted to three samples wearing a "seeded" label. A parameter the ledger records
  nothing about still prints, with a sample size of zero, no recommendation and the reason it
  has none: `stall_after`, `quiet_after`, `max_agent_processes`, `[worktree] concurrency` and
  the two calibration bounds are all in that state, and a bound nothing records is a bound
  nobody can tighten. **It is advisory and writes nothing** — applying a recommendation stays
  a human's or a gate's call (basicly-3ifz.1).

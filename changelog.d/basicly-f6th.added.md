- **`basicly tracker shadow` runs the work-tracker cutover's shadow differential against the
  live tracker.** Step 2 of `docs/design/work-tracker.md` §5 had every piece of machinery and
  no driver: the comparison could only be constructed by a test. The command folds the owned
  event log under `.basicly/ledger/` and holds its answers to phase derivation, the ready set
  and gate status against `br` itself — `br list -a`, one `br show` per hundred ids, and
  `br gate list` for the query no export can answer, since a `gate report` row is absent from
  the JSONL export. The reference is live and never a re-import, which the kit enforces by
  perturbing the ledger and refusing a source whose answers move with it; the reference
  therefore re-reads the tracker rather than caching, because a memoised answer would clear
  that probe without being independent. The run writes to neither store and reports `clean`
  and `conclusive` as two verdicts, so agreement on a query every record answered identically
  cannot be read as evidence. First run against this repo's 643-record ledger: 331 gate
  disagreements (no export carries a gate row, so the import could not have carried one), one
  phase disagreement on a bead whose worktree binding never reached the committed export, and
  three records the tracker holds that the ledger does not (basicly-f6th).

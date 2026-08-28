- **`ledger-fsck` now reports a label written one character at a time.** A record's `labels`
  field is stored as a list or as a comma-joined string, and reading the second with a `for`
  turns `phase-2` into seven labels no `--label` selection matches. The kit's split now lives
  in one place and `fsck` reports a log carrying the class as `broken` (basicly-0cpn51).

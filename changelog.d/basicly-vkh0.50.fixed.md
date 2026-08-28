- **`basicly tracker write` no longer says `recorded:` for a write the ledger did not keep.**
  The seam now answers which facts landed, the command names those instead of echoing the argv,
  and a write the log does not hold on a re-read - or one that could not take the lock - fails
  saying what was not recorded (basicly-vkh0.50).

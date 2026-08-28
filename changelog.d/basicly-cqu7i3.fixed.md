- **A concurrent tracker write is no longer lost to the identity scrub.** `scrub_ledger`
  rewrote an event log whole - read, temp file, rename - holding no lock, so an event appended
  in that window vanished silently: the rename succeeded and the log still parsed. It now takes
  the same `LedgerLock` an append takes (basicly-cqu7i3).

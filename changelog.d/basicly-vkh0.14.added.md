- **The owned tracker's record snapshot: a fold you can keep, and prove stale without
  folding.** `.basicly/core/kit/tracker/snapshot.py` derives every record's state from
  the event log and writes it as a local, gitignored artifact anybody may delete. Its
  first line carries the log's tip event id and line count, so a reader dates the
  snapshot by *scanning* — counting newlines and decoding one line — instead of folding.

  Two details are what make that safe rather than merely fast. The recorded id is the
  log's **tip**, the last line of the last file, not the canonical maximum: canonical
  order sorts by record then sequence, so its maximum is the highest *record id*'s last
  event, which no cheap read can find. And the scan is taken **before** the fold, never
  after — scan-first under-reports and reads as stale, while scan-second would claim to
  have folded a line it never saw and read as *fresh*. The invariant is one-directional:
  a cheap check may say stale when it is fresh, and may never say fresh when it is stale.

  Rotation is by period and **archives everything, pruning nothing**, because folding the
  whole history is a requirement. It writes one new empty file whose name sorts last,
  which is all the append target looks at, and publishes a checkpoint carrying every
  item's totals — including an item idle since before the boundary, which is what bounds
  steady state to one checkpoint plus the current file. The period is an argument, never
  a clock read.

  Nothing repairs a derivative: an unparseable snapshot or checkpoint is replaced from the
  log, because a repaired cache is a second source of truth wearing a green tick
  (`basicly-vkh0.14`).

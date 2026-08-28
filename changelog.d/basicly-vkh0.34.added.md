- **A leaked disclosure can be withdrawn from the tracker ledger, and only that path may rewrite
  it.** `events.withdraw` takes one event's free text out, keeps every value the fold reads by
  name, re-mints the id and appends a `withdrawn` event whose time and reason the fold reports.
  `fsck` now reports any line edited in place by anything else (basicly-vkh0.34).

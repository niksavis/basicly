- **A tracker `fsck --rebuild` that would lose records is refused, not reported clean.** The
  shrink guard could not fire through `fsck.rebuild`, which unlinks every derived file before
  writing, so a ledger whose logs had vanished rebuilt to a 0-record snapshot over a 2-record
  one and exited 0. It now compares before it deletes, and a refusal removes nothing.

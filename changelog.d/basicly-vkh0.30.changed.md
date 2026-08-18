- **The owned tracker's event vocabulary splits: `note` carries prose, `checkpoint` and `artifact`
  are typed machine state the fold reads by name.** One kind carried both before — 2,667 of this
  repository's 5,752 ledger events are `comment` [measured 2026-08-18], holding the prose a human
  wrote *and* every marker the loop derives state from — so a reader could not select machine state
  without grepping a free-text body, and the fold could not refuse a malformed marker.

  A folded record now answers two more questions directly: `checkpoints` maps an approved
  checkpoint to the approver the event named, and `artifacts` maps a handoff artifact kind to the
  last body recorded under it. Both are carried in the derived `snapshot.jsonl` and in a rotation
  checkpoint, because the resumed fold reads a checkpoint rather than the archive: one that dropped
  `checkpoints` would read an approved item as never approved. An artifact body sits outside the
  free-text cap, so it is stored whole rather than cut at 4,096 bytes.

  **`comment` is aliased, never retired, and no line on disk changes.** The log is append-only, so
  every `comment` event stays exactly as it is and folds to the same work log a `note` folds to —
  asserted as state equality between two ledgers written in the two spellings, because the
  unknown-kind skip path would have silently dropped the prose history and the checkpoint markers of
  every item older than this change. The kit's own writer records `note` from now on; the `comment`
  subcommand keeps its name, which is a consumer surface and moves under its own window.

  **What this does not do.** The `br` mirror seam still writes `comment`, deliberately: the reader
  must accept both spellings before any writer switches. The remaining kinds of the specified
  vocabulary — `decision`, `scope`, `wait`, `grant`, `rework`, `sizing`, `classification` — and the
  reader that resolves an existing marker body to its typed kind are not here; nothing yet reads a
  checkpoint or an artifact off its kind rather than out of prose (`basicly-vkh0.30`).

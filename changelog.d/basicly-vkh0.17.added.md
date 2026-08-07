- **The existing tracker imports into the owned log, with deletion as a first-class event.**
  `.basicly/core/kit/tracker/migrate.py` reads a `br` JSONL export and writes the events it
  implies, stamping every one with its provenance and the source snapshot's name, and each
  `created` event with the export's sha256 — so a later reader can say which snapshot a
  record arrived from. That digest is what the shadow differential's sharpest refusal checks.

  **An upsert-only export cannot express a deletion**, which is why tombstones are a
  first-class concern rather than a detail. A record the snapshot merely omits is *reported
  as absent*, never deleted; a deletion has to be **stated** by the caller, and it becomes a
  `tombstone` event carrying the same provenance as any other. A tombstone is refused for a
  record the snapshot still asserts, so a deletion arrives as a later import whose text no
  longer carries it.

  A record the log already holds is not created again, and a divergence between what the log
  holds and what the snapshot says is reported rather than overwritten — the import is a
  translation, not an authority (`basicly-vkh0.17`).

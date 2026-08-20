- **The two vocabularies sharing the provenance key are reconciled, so every folded edge is
  accounted for.** `migrate.PROVENANCE_KEY` and `provenance.KEY_LABEL` are literally the same
  string: the engine's write seam stamps *who wrote the event* into the field the fold reads as
  *how strong the evidence is*. Two axes, one name, and never reconciled. Measured on this
  repository's log: 142 edge events carried `engine` or `dual-write`, folding to 133 edges
  disposed `decide` for want of a vocabulary rather than for want of a fact, which is why
  `gating_edges` read 932 of 1065. It now reads **1065 of 1065**, and `unknown_labels` is
  empty.

  The two writer identities gate because of what they mean, not as a convenience: an event the
  engine's own seam appended is one a command asked for, which is the claim `EXTRACTED` makes.
  This widens the gating set by **two exact strings** and keeps the rule that only an exact
  known string gates - a near miss - `engine` with a trailing space, `dual-writer` - still routes a decision, and
  a test asserts that, because a prefix match here would be a fail-open on the one gate that
  decides whether an edge may hold up a landing. The blast radius was measured before the
  change: `gating_edges` has no production consumer, so resolving the 133 could not start
  gating anything today.

  They are counted in a new `EdgeFold.writer_labels` rather than folded into `unknown_labels`,
  because an edge that carried a writer identity never carried an evidence label and one count
  for both would say it did. The agreements the kit cannot enforce for itself - it may not
  import `basicly` - are pinned from the test side, which is the only place that can see both:
  `WRITER_LABELS == {owned_write.OWNED_PROVENANCE, mirror.MIRROR_PROVENANCE}`, and
  `KEY_LABEL == migrate.PROVENANCE_KEY`, so a later split of the key fails there first.

  This needed `provenance.py` split: it sat at 7,890 tokens, exactly its frozen baseline, so
  the vocabulary could not gain an entry. It is now 6,686 with the vocabulary and the payload
  key names in `labels.py` at 2,979. **A real gain beyond the size:** `provenance` and
  `differential` read one `DIALECT_KEYS` table instead of two copies, which is
  `basicly-oii83r`'s root cause removed rather than patched on both sides. Two standalone-kit
  fixtures enumerate the files a consumer copies, and both refused the new module until it was
  named - the control working, on the one constraint the kit cannot check from inside
  (basicly-493g5f).

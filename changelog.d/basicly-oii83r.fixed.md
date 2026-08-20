- **The differential's fold reads an edge in either spelling the log holds, and says which one
  it read.** `provenance.fold_edges` required `target`/`edge_type` while `migrate.py` writes
  `from`/`to`/`type`; `basicly-svct4w` fixed that side and asserted the mirror in a test so it
  could not be forgotten. `differential.views_from_events` had the defect the other way round:
  it read the engine pair only. Measured before the fix, against a positive control - four
  edges in the declared spelling read as **0**, the same four in the engine's as 4. The record
  predicted 1, and 1 was right about the fixture it came from, which holds three declared edges
  and one engine edge; 0 is what an all-declared fixture returns, because a reader matching
  neither key returns nothing rather than something.

  The pair table is read **out of `provenance`** rather than respelled here. A second copy of
  it is exactly how the two folds came to read different populations of one log, so a new
  by-path sibling loader was cheaper than the duplication. `edge_dialects` reports which
  spellings a log carries, for `EdgeFold.dialects`' reason: an empty edge set is otherwise the
  same answer for a log with no edges and a log whose every edge the reader could not parse,
  and those are opposite facts. A payload in neither spelling is still dropped rather than
  guessed into an edge.

  **The test that pinned the defect is now the control that both folds agree.** It asserted the
  1 deliberately and said in its docstring that the asymmetry belonged to the other module;
  that assertion now reads `== len(edge_fold.edges)` and compares the dialect reports directly,
  so a reader that *switched* spellings instead of accepting both still fails.

  This needed the module split first: `differential.py` sat at 11,110 tokens, exactly its
  frozen baseline, so not one line could be added. It is now three modules - the owned fold and
  the audit at 8309, the pure derivation at 3597, and the five records both sides report in at
  1236 - with the one-way direction of both seams checked by a cross-reference scan rather than
  assumed. Every name a consumer reads is re-exported by alias, so `except DifferentialError`
  and `kit.is_ready` are unchanged across fifteen call sites (basicly-oii83r).

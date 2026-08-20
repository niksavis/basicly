- **A `harness-board/v1` snapshot producer that reads files and spawns nothing.**
  `board_snapshot.build_document(repo_root)` folds a conformant snapshot out of three sources —
  the owned event log, `.basicly/usage/run-records.json` and `.basicly/usage/verify-run.json` —
  in **zero subprocesses** and **one** fold of the log, measured by a spy in
  `tests/test_board_snapshot.py` rather than asserted. The producer exists because
  `supervise.observe()` folds the same log **93 times** to answer one question, at 6.1 s; a whole
  snapshot on this repository's committed corpus is **81 ms** (median of 7) against a 500 ms cap.
  Nothing consumes it yet — `basicly board --out` is a later unit — so this adds a library surface
  and no command (`basicly-rn0o.2`).

  **The live-lock facts are an argument, never a read.** Reading the supervisor lock here would
  mean calling `supervise.read_holder`, and the supervisor emits a snapshot itself, so the import
  would close the cycle `supervise → board_snapshot → supervise`. Callers pass a `SessionFacts`
  carrying `supervise.LockInfo`'s own field names, and with none supplied the `session` section is
  **omitted** rather than filled with nulls or a guessed root.

  **Omit, never estimate**, because the schema has no field marking a value as estimated. A
  transcript-estimated dispatch is left out of `spend`, and where every dispatch is an estimate the
  section is absent rather than indistinguishable from a billed one. In a lane worktree
  `.basicly/usage/` does not exist, so `spend`, `health` and `gates` are all absent and the tracker
  half of the board still draws. `lanes`, `units` and `graph` are not emitted by this producer:
  `lanes[].phase`'s authority is `loop_state.read_node_state`, which needs a source outside this
  producer's three files.

  **The marker roster is bound by a gate, not by a hand-kept list.** `board_fields.MARKER_FAMILIES`
  must equal `.scripts/check_marker_families.FROZEN` — 11 declared plus 1 retired — and
  `tests/test_board_fields.py` asserts that by loading the gate **by file path**, since `.scripts/`
  is not an importable package and its gates import into `basicly`. All 12 are parsed, the retired
  `[harness-overrun]` included, and a malformed marker is skipped rather than raised.

  **A pending ask is a pairing, not a tally.** Reading every `[harness-wait]` request as open
  reports **140** on this repository's log against **1** genuinely pending; the test pins both
  against a frozen corpus under `tests/fixtures/board/ledger/`, with the answered side at **203**
  distinct ids so a parser that silently matched nothing cannot pass. Every string in the document
  passes `redact.redact_committed`, so no absolute path and no username reaches a board.

- **The board snapshot schema's field-selection figures now name the store this repo has.** Two
  `description` strings quoted `3336549 B` against `33745 B` — `98.9×` — which are the deleted
  external tracker's bytes. Against the owned ledger it is **5890340 B** against **44454 B** for
  the 236 active records at six selected fields, **132.5×**. The rule is unchanged; only the
  measurement behind it was stale (`basicly-rn0o.2`).

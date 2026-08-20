- **The board snapshot schema no longer publishes a spend figure nothing here can re-derive.**
  The `spend` description stated 953.82 USD over 357 dispatches, measured 2026-08-14. Not the
  deleted store's number, so it fell outside the record that repaired those - and not
  re-derivable at all: `.basicly/usage/` is git-ignored and **nothing under it is tracked by
  git**, so no gate could ever notice the figure drifting. Proved rather than asserted, and the
  proof produced a third number: the design document says 431 dispatches, the schema said 357,
  and this checkout holds 321 records. One quantity, three figures, none checkable, changing
  per machine and per moment. The description now says what the field is and why it carries no
  figure, and points at `docs/requirements/harness-board.md` where the motivating measurement
  is dated and attributed.

  **The record's first criterion sent me looking for the others, and they were stale too.** A
  scan of every description in the schema found five more measurement-shaped spans. Those are a
  different case - the ledger *is* tracked, so they are checkable - and they had drifted: the
  event log read 5,890,340 B against an actual 6,396,125 B, **+8.6% in six days**. Re-derived
  and re-dated rather than removed, because a derivable figure satisfies the criterion's first
  branch. The field-selection ratio moved 132.5x to **156.2x**, so the claim was stale in the
  direction that made the argument weaker than the truth, and both figures now carry the date
  they were derived on plus the previous reading, so the next drift is visible as a delta
  rather than as a surprise.

  One correction along the way, and it is the same shape as the defect: my first count of the
  active rows said 237 against the schema's 236, because I counted `snapshot.jsonl`'s header
  line as a record. The schema was right and my instrument was wrong (basicly-fxdrcf).

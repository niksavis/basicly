- **`fsck` and `rebuild` make "the log is the truth" a claim you can check.** Without a
  check that folds the whole log and reports what it finds, and a rebuild that regenerates
  every derivative from the log alone, that sentence is untestable. Both now exist in
  `.basicly/core/kit/tracker/fsck.py`, and the report sorts its findings by **what fixes
  them** rather than only saying *bad*:

  - a defect in the **log** — `rebuild` cannot touch it, because repair is by appending a
    corrective event and never by editing a line, or the checker quietly becomes an editor;
  - a **derivative** that disagrees with the log it claims to summarise — fixed by
    replacement;
  - a **warning** for an event kind this version folds no state for, never a failure, so an
    old reader meeting a newer ledger does not report false corruption.

  A *stale* derivative is deliberately not a finding: every reader regenerates on a stale
  read, so lagging is the design working. The case that matters is the one the cheap check
  cannot reach — a derivative whose header agrees with the log and whose **body** does not
  — so that is the one place a fold is spent on a derived file.

  Two findings are suppressed rather than reported, both for one reason: a forked record's
  carried totals are void until a fold restates them, so a totals disagreement there is the
  fork's consequence, not a second defect. Reporting the consequence beside the cause is how
  a report of eleven findings hides its one root (`basicly-vkh0.15`).

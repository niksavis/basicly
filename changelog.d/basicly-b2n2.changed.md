- **The `harness-loop` skill now opens with the tracker-write habit that a measured data loss
  earned.** Run `br sync --status` before any `br` write on a checkout you did not just leave,
  and `br sync --import-only` first if it reports the committed export is newer. A mutating
  command on a checkout in that state auto-flushes the *older database over the newer file*:
  measured once at a 426-record database published over a 612-record export, deleting 187
  records, 47 of them open, while `br create` reported success and no gate fired.

  Two details are what make it a habit rather than a note. The export is recoverable only
  because it is committed, so git is the backstop and the database is the side that gets
  corrupted. And the status line is computed from *timestamps*, so it also says the export is
  newer on a healthy checkout where the content is byte-identical and the import is a no-op —
  which is exactly how people learn to ignore it. The skill also asks you to check the shape of
  a tracker diff before committing: filing two beads is `+2` lines, so large deletions mean this
  is in progress.

  **This is guidance and a regression test, not a fix.** The underlying defect is still open
  (`basicly-b2n2`); what shipped is the habit that avoids it and a gate holding the requirement
  that a publish which would shrink the export must be refused rather than silent, so the
  eventual fix cannot land without satisfying it.

- **A worktree's tracker redirect is resolved in exactly one place, so the read and the write
  cannot reach different checkouts.** A lane's worktree carries a one-line `redirect` naming the
  checkout that owns the tracker, and the rule for reading it was implemented four times inside
  the package under three different rules, with the landing's own id check not resolving it at
  all. Two of the four already disagreed: one honoured a redirect only when the target directory
  carried the expected name, the others honoured any directory, so a redirect naming anything
  else sent the tracker read to one checkout and the event-log write to another — the failure
  that had already silently discarded every tracker call made from inside a lane.

  Measured on a temporary repository whose redirect names a differently-named directory: two
  readers answered `base/tracker` and `wt` before and both answer `base` after, and the
  landing's id set answered the worktree's own ids in every redirected case, control included.
  Two further defects the tests found on the way — an empty redirect file resolved to the
  **process working directory**, because `Path("")` is `Path(".")` and reads as a directory, and
  the one resolver refuses it; and a JSON array line in the tracker export crashed the id read
  with `AttributeError` where another of the four copies had skipped it. The standalone hook
  script keeps its own copy, which cannot import the package, and a parity test holds it to the
  same rule (basicly-tcmy.19).

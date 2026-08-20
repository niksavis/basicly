- **A repair brief the branch has moved past is refused as stale, and a repair that committed
  nothing is named rather than charged.** Observed on `basicly-gvlpxm`: its worktree still held
  a brief asking for the *post-regeneration branch head*, which had been fixed and landed hours
  earlier as `merge.MergeResult.landed_head`. Advancing would have dispatched a full metered
  repair for finished work. Nothing invalidated a brief when its defect closed by another
  route - not the landing that carried the fix, not the gate, and not the brief's own reader.

  **The wedge, which is why this was not merely wasteful.** A repair that finds nothing to do
  commits nothing, so the branch carries nothing its base does not hold, so the next advance
  takes the same branch and the same brief again. The brief is consumed on read, so the
  following advance falls through to `_rework` and charges the last slot for a round with
  nothing in it.

  The signal needed no clock. A brief now records the branch head it was written against, and
  a head that has moved means that work landed by some other route - the only fact that changes
  when work lands. Both halves fail **quiet** on anything short of proof: a brief written before
  the field existed carries no head, and a ref that will not resolve answers None, and neither
  is evidence of staleness. Refusing a repair on the reader's own uncertainty would strand work
  a red gate really does owe, which is the opposite failure and the more expensive one.

  **Where the code went was decided by a linter, and it was right.** `_repair_in_place` sits at
  exactly the six-return budget `ruff` PLR0911 allows - the same budget that forced
  `_repair_outcome` out of it under `basicly-dbbh` - so the staleness refusal widens the
  existing early-out rather than adding a branch, and the committed-nothing check went into
  `_repair_outcome`, whose stated job is what a finished repair leaves the loop blocked on. Both
  refusal messages live in `repair_brief.py` beside the predicate that raises them rather than
  at the call site, and a `landed=(branch, head)` tuple collapsed to a branch name once it was
  clear the brief already records the head to compare against (basicly-59fkfu).

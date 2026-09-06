- **A merged lane no longer reads as an empty worktree.** `landed` joins the lane states: a
  branch whose commits base already holds now says the work merged and the worktree awaits
  teardown, instead of sharing `queued` with a worktree that has done nothing
  (basicly-k6tpep.4).

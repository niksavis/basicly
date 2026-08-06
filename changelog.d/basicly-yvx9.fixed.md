- **A lane killed by `[runner] runner_timeout` no longer loses its work.** The kill
  takes the agent out before its last step, which is the commit — so the harness now
  commits whatever the worktree holds and lets the landing judge that diff, because a
  timeout is the harness's own decision and is not evidence against the change. The
  three killed runs on this repo's ledger discarded 47.8M tokens of it, nine tenths of
  everything ever paid for work that did not land, one of them a finished change that
  passed every check when it was committed by hand. Judged, never trusted: a red gate
  reworks the lane with real findings where an uncommitted tree could only produce
  "not-ready" and a second full dispatch, and the stall decision item is still queued
  either way, so a timeout stays visible to a human (`basicly-yvx9`).

- **Concurrent lane dispatch no longer loses three lanes out of four to the base-checkout
  commit, and a lane that queues for it is told so in those words.** Every `basicly loop run`
  publishes its claim by committing tracker state in the *base* checkout before it provisions
  a worktree — one index and one HEAD, shared by every dispatch, and nothing guarded it.
  Observed 2026-08-19: four dispatches started in the same second, one committed and three
  exited non-zero having done nothing, two on `git commit`'s exit 1 (a peer had already
  committed the same dirt, so nothing was staged) and one on exit 128 (a peer held
  `.git/index.lock`). So the factory's fan-out width was bounded by an unguarded serial step
  rather than by the isolation model it advertises. Reproduced against the pre-fix code path
  with the interleaving injected rather than raced: three of four dispatches failed, with
  exactly the message the incident recorded.

  `merge.commit_tracker_state` is the single funnel every one of those dispatches goes through,
  and it now holds a file lock (`basicly.base_lock`) across the whole read-then-commit window.
  A loser **waits** for the holder instead of racing it, and because the status is read inside
  the lock a loser finds the tree its peer left — its own claim already published — so it
  declines rather than recording the claim twice. A dispatch still queued after the budget
  fails with a message that names *contention*, the holding pid and how long it waited, because
  half of this defect was that `Error: command failed (1): git commit` reads as a rejected
  commit and sent an operator into the hook chain.

  **Stated failure mode:** liveness is the lock file's mtime and nothing refreshes it, since
  the critical section is one gated `git commit` with no thread to beat from. A commit slower
  than the hold budget is declared crashed and its lock taken over — which costs that one lane
  the pre-fix behaviour, loudly, on work it has not started yet (basicly-kjc5.63).

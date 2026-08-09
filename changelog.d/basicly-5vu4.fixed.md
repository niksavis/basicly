- **A landing no longer silently discards a lane's merge resolution.** `git rebase` skips merge
  commits unless `--rebase-merges` is passed, so a lane that resolved a conflict with
  `git merge` — producing content held in neither parent — had that content deleted while the
  rebase reported success and exited 0. It happened twice in one session here, and on one of
  them the test suite stayed **green** afterwards, because the feature and the tests covering it
  were dropped together: a consistent tree that no longer did the thing it shipped. No gate can
  catch that shape, because nothing is left to fail. The merge queue now refuses such a branch
  before rebasing it, naming the merge commit and telling the lane to linearize; the lane keeps
  every commit it had. A second guard compares the tracked paths either side of the replay and
  restores the branch if anything was lost to a cause nobody enumerated, so the queue can no
  longer both drop work and report success.

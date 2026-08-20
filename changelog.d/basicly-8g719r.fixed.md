- **`basicly worktree cleanup` decides on content instead of ancestry, so it stops reporting
  every correctly landed worktree as unmerged.** The check was `git branch -d`, which answers
  ancestry, and ancestry is not what makes a branch safe to discard: a lane that queued behind
  another is replayed onto the base it finds, so its commits arrive under new shas and the
  original ref is not an ancestor even though base holds every line. Cleanup relayed git's
  `not fully merged` as *unmerged — re-run with force to reclaim*, and **a check that is wrong
  on every correct case teaches an operator to pass `--force` without reading it.** On
  2026-08-20 that habit came within one command of discarding a commit base genuinely did not
  hold. Worse, `git branch -d` also fails for reasons that have nothing to do with merging —
  a branch still checked out in a worktree gives `cannot delete branch … used by worktree`,
  and `-D` refuses that too, so the offered remedy could not have worked.

  Cleanup now compares content: the paths the branch changed since the fork point, against
  what base holds at those paths. Base holding all of them reclaims the session with no
  `--force`. **Anything else refuses and says which**, in four distinct sentences rather than
  one — base is missing named paths and force would discard them; the comparison could not be
  made; no session record names the base; git refused the delete outright. Only the paths the
  branch touched are compared, so a sibling lane's landings are not mistaken for missing work,
  which would be the same wrong-every-time answer pointing the other way. `git branch -d`
  stays as the fast path, so an ordinary merged branch still costs one git call, and
  `--force` keeps its old meaning: delete regardless, no question asked (basicly-8g719r).

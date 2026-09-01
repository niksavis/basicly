- **`runner.runner_timeout` is raised from 3600s to 7200s.** `basicly usage tuning` advises
  7202s over 50 measured dispatches, and one lane streamed events for the whole 3600s before
  the clock killed it with an empty worktree - the failure that took the bound 1800 to 3600
  once already. `quiet_after` and the spend ceiling still bind first (basicly-hnnmk9).

- **The Hold and Kill gate verbs now do something.** Every escalation the supervisor raises has
  always offered `park` as a route and nothing anywhere carried it out, so an operator who parked
  a lane watched the next pass dispatch it again; `kill` had no surface at all. Answering an
  escalation `park` (or `hold`) now sets the lane `deferred` and records the reason on the bead,
  which is what makes `loop_state.is_dispatchable` refuse it and stops it holding its parent open
  — so it is human-only, like `land anyway`, and a delegated answer says plainly that it parked
  nothing. New `basicly loop kill <id> --reason "<why>"` tears the lane's worktree down and closes
  the bead won't-do-this-way. Run bare it refuses, mints a one-time code and writes nothing: kill
  is the only verb that removes a *requirement*, so a human is required at every integrity level
  and neither an autonomy grant nor an interactive terminal substitutes for the relay. The
  teardown runs before the close, so a refusal can never leave a closed bead bound to a live
  worktree, and committed work is left on the `harness/` branch unless `--discard` is passed.
  The requirements document's §5 blamed this on the status vocabulary — `deferred` was already
  excluded from `DISPATCHABLE_STATUSES` — and now records the correction with the real gap
  (basicly-u2hl.3).

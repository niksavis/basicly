- **Two shipped skills absorbed process traps that had to be re-learned to be believed.** Both
  cost a real session, and both are the kind of thing a rule cannot convey by warning about it
  in general terms — so each is now a named section with the commands that work.

  The `harness-loop` skill gained a *Watching a lane* section. A dispatched lane is a
  subprocess of the engine rather than a subagent of the driving session, so nothing in an
  agent's own tooling lists it; the section carries the four read commands that do answer it,
  the rule that a worktree name replaces dots with hyphens (so watching the dotted path reports
  "no worktree" forever), and both ways process-polling fails. `pgrep -f <pattern>` and
  `pkill -f <pattern>` match the *caller's own* command line: the kill signals the invoking
  shell and the target survives, and an `until ! pgrep -f …` wait can never exit — it spins to
  timeout and reports the job as still running long after it succeeded, which is the damaging
  half.

  The `session-finish` skill now requires a durable artifact to be written straight to its final
  path, and forbids a background process outliving the session. The scratchpad is cleaned
  mid-session, which cost an 846-line design document and the transcript that produced it
  (`basicly-yjwu`).

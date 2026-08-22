- **A lane that is running no longer renders as one that has done nothing.** The board's
  in-flight card carried five of the sixteen properties the snapshot contract declares, so
  five of its six cells read `not measured` while the supervisor's own terminal printed
  those very figures for that very lane in the same second. The cause was the tier it read
  from: the card was built from the tracker binding alone, and that binding holds only the
  **last finished run** — which a lane on its first dispatch does not have.

  Three tiers now supply it, each asked for what it actually holds. The live event stream
  holds what a running lane has spent and the last thing it said; it is process-local to
  the supervisor, so it answers where the producer is the supervisor's own tick and is
  empty elsewhere rather than zero. The last run record holds a finished dispatch's cost,
  occupancy and duration exactly. The tracker binding holds the branch, the status and the
  agent.

  **A live lane does not inherit the previous dispatch's figures**, and that is the whole
  care in the change. Cost and occupancy are per-dispatch, so carrying them forward prints
  last run's spend under a heading that says the lane is running now. The agent and the
  model do carry, because a lane keeps its runner between dispatches.

  The activity line is the field with no substitute. Elapsed time and spend say a lane is
  alive and expensive without saying whether it is stuck.

  One rule was almost broken by its own implementation. Tokens has two sources and the
  live one wins while a lane runs, and preferring it on *truth* rather than on *presence*
  handed one window straight back to the previous dispatch: a lane's stream is published
  the instant the dispatch starts, so it reports a real `0` until the first turn is
  metered, and a falsy test resolved that to the last run's total — a card reading ten
  million tokens in a lane's first second. Tokens now obeys the same rule as cost and
  occupancy, and two tests pin it, one of them the window with a previous run to fall back
  to. The falsy form kills two of the three, so they discriminate on the rule and not on a
  value.

  Live elapsed time is deliberately still absent: no start time exists on the lane's
  stream, on its tracker view, or anywhere on disk, because the run record is written after
  the process ends. That is recorded as a follow-on rather than left as a silent gap.

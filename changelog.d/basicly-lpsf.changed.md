- **A lane is now bounded by what it spends and whether it is alive, not by the clock.**
  `[runner] runner_timeout` was the only terminal bound the engine had, and it is the one
  signal that says nothing about whether work is happening — so it had been calibrated
  inside the upper tail of real work, killing lanes that were fine: the longest
  *successful* lane on this repo's ledger ran 1712s against an 1800s cap, 95.1% of it,
  with 10 of 68 successes finishing past 80%. Two bounds replace it, both read off the
  per-turn event stream every metered dispatch already emits (`basicly-rupz`). A new
  `[runner] quiet_after` (default 1800s) kills a dispatch whose stream has gone silent,
  which is proof of a wedge in a way an unchanged worktree never was — an agent thinking,
  or waiting on a long test run, writes no file but still emits. And the D3 grant ceiling
  now binds *during* a dispatch rather than only between passes: `spend_status` was read
  before a pass and written after it with nothing in between, which is how a 20,000,000
  token grant was overshot to 22,164,783 by lanes that were still in flight when the check
  ran. `runner_timeout` stays, terminal, moved back to its 3600s default and demoted to a
  backstop for what neither new bound can see — a process that hangs holding the pipe, or
  a stream that stops while the process does not exit. A killed lane's run record now names
  which bound stopped it, so `quiet_after` — declared without a measurement, because until
  now the stream was paid for and discarded — can finally be calibrated against evidence
  rather than re-declared (`basicly-lpsf`).

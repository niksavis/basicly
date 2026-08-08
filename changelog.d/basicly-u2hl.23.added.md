- **BUILD's downstream-WIP entry predicate now exists and binds.** Requirements 3.1 states
  BUILD's entry condition as *plan gate green **and** downstream WIP below limit*, and only
  the first half was implemented: `[worktree] concurrency` bounds how many lanes run at once,
  and nothing bounded how much finished-but-unlanded work piled up behind them. A supervised
  pass that landed five lanes faster than anyone reviewed them produced five lanes' worth of
  unreviewed surface, and neither the spend ceiling nor the concurrency cap could see it —
  the quantity that actually runs out is review capacity, counted in units rather than
  tokens. `[policy] max_downstream_wip` (default 5) is that bound: `basicly.wip` counts the
  session's units parked in `verify` or `ship` — the same population `advance_parked` drains
  each pass, so the bound cannot wedge — and a pass starts only what the remaining headroom
  admits. Lanes past it are returned unstarted and `refused`, so they route to the decision
  queue rather than burning a rework attempt, and each says which limit holds it and which
  units to land to clear it. Reported on every pass, refused or not (`wip: 2/5 unlanded
  downstream of build; …`), because an unbounded pass must never again look like a checked
  one; a pass the bound holds entirely also queues an escalation on the session root, so a
  client reading only the queue does not see it as an idle pass (basicly-u2hl.23).

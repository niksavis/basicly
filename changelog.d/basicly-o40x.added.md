- **A supervisor can be stopped without killing the lanes it has in flight.**
  `basicly loop supervise` ran `while True` and returned only when every child of the
  root had closed, so the only lever short of the session finishing was a signal — and
  the lanes are `claude -p` subprocesses of that process, which a signal leaves killed
  mid-write or orphaned against a grant nothing is metering. Lock takeover was not the
  control it looked like either: a lock is stolen only from a holder whose heartbeat has
  gone stale, so a *working* supervisor could not be asked to finish. Two bounds now end
  a session between rounds, where nothing it started is still running.
  `basicly loop stop <root> --reason "<why>" [--by NAME]` writes a marker naming the
  requester and the reason, prints the lanes it is waiting to land, and returns once the
  session does: the round in flight completes, every dispatched lane lands, and no
  further lane is seeded. It refuses when nothing is supervising that root, because an
  unread marker would stop the next session started there before it ran a round.
  `basicly loop supervise --max-passes N` is the cheaper half — it returns after N
  rounds even with open children left, so a launch can commit to a bounded spend up
  front. Both exits are non-zero and name themselves on the pass narrative
  (`stopped:  …`), which is where the requester and reason are recoverable afterwards
  (`basicly-o40x`).

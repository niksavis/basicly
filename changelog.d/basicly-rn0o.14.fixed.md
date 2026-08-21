- **A board snapshot's lock age is read by the supervisor's own reader.** The
  snapshot reported a holder heartbeat age derived independently of the code that
  decides liveness, so the board could disagree with the supervisor about whether a
  lock was stale (`basicly-rn0o.14`).

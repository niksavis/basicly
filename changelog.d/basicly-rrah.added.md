- **Every supervised dispatch now leaves a transcript.** A lane's `stream-json` output was read
  into memory, spent entirely on token accounting and dropped when the process exited: measured
  on 2026-08-08, 32 dispatches costing $122.41 left records of what each one cost and nothing of
  what it did, so no claim about lane behaviour could be evidenced after the fact. Each dispatch
  now writes `.basicly/usage/lane-logs/<session>/<bead>.jsonl` as the events arrive, flushed per
  event so a lane stopped by a quiet bound, a spend ceiling or a hard kill keeps what it had
  already said. The supervisor's own narrative — the session header, every dispatch line and
  every routed outcome — is teed to `pass.log` in the same directory, where before it existed
  only in a terminal pane. Both are redacted, both sit under the self-ignored `.basicly/usage/`
  tree, and `[runner] lane_log_sessions` bounds how many sessions are kept before the least
  recently written rotate away.

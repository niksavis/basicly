- **The supervisor pass line now states each runner's own health and drift.** `basicly health`
  scored every agent off the run-record log and nothing in the engine read it, so a runner whose
  failure rate had moved was visible only to whoever ran the command — never to whoever reads a
  pass, where the band and spend numbers already are. `supervise.health_coverage` adds two lines
  beside them before anything dispatches:

  ```text
  health:   claude 0.78 over 163 runs (fail 18%, rework 17% — 18 bead(s) re-dispatched); manual
            0.99 over 194 runs (fail 0%, rework 2% — 3 bead(s) re-dispatched)
  drift:    REGRESSED claude: fail 80% over the recent 5 vs 16% over 158 baseline runs (+0.64)
  ```

  That is this repository's own log on 2026-08-14, not an illustration.

  It is **observability, not a gate**: a pure in-process read over
  `.basicly/usage/run-records.json`, so it spawns nothing, meters nothing and refuses no lane.
  D23 (`docs/requirements/factory-loop.md` §15.7) makes a signal with no recorded correct firing
  reportable rather than blocking, and this one has never fired in anger. The drift half prints
  both window sizes, because the flag only means anything with enough runs on each side of it.

  A repo with no log says so — `no run-records yet` — rather than printing a zero (`basicly-zdtx`).

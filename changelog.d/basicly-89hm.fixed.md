- **The context window a dispatch is metered against is read off the adapter's own stream, so
  a consumer stops inheriting a stale constant.** `runner.py` shipped `claude: 200_000` while
  this repository's own `basicly.toml` raised it to 1_000_000, so a repo that installed the
  harness and did not hand-write `[runner.context_windows]` metered against the very figure
  whose staleness had put the finalize trigger at a fifth of its intended point here — lanes
  recorded occupancies up to 223_221 against a declared 200_000, and the override hid that
  from anyone measuring locally.

  **The remedy is not a bigger number.** Probed against claude 2.1.233 on 2026-08-15, a single
  dispatch reported *two* windows on its own stream — `claude-haiku-4-5` at 200_000 and
  `claude-opus-5[1m]` at 1_000_000 — so the window is a property of the model, not of the
  adapter, and no per-adapter constant can be right for both. `context_window.resolve` is now
  an order of preference: a window you declared wins, because the record has to explain the
  threshold the engine acted on; then the window the adapter reported for this dispatch,
  resolved by the model of the final turn rather than the first or the largest; then a dated
  shipped default; then a refusal. **`unmetered` is a real recorded answer**, not a fallback:
  codex and copilot report no window at all — established against positive controls, codex's
  `turn.completed` usage block is present and carries none, copilot's `modelMetrics` is
  present on 6 of 6 local stores and carries none — so neither ships a figure, and a dispatch
  on either records that it could not meter rather than assuming one.

  Every shipped default now carries the probe that read it and the day it was read, and
  `stale_declarations` fails a default that has neither, that disagrees with its own recorded
  probe, or that is past a 180-day re-read bound. That is a calendar falsifier: the existing
  one needed a lane to record a contradiction first, which means paying for it
  (basicly-89hm).

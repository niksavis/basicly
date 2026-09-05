- **A failing check's own words now survive the terminal.** The gate streamed output and
  captured none, so a `pytest` flake left only *"output streamed rather than captured"* and
  its identity was gone. `run_check` now tees — it forwards each line exactly as before and
  keeps it — and writes the redacted tail to `.basicly/usage/` (basicly-zlqn7e).

- **A record may no longer be closed against a demonstration command that selects no test.**
  The plan gate checked the `demonstration` field's *form* — present, one line, something
  backticked — and never ran it, so a `uv run pytest <file> -k <expr>` whose expression matches
  zero tests passed. Measured over one session, five records were closed or worked naming
  exactly that, against positive controls collecting 210, 142, 87 and 23 tests in the very files
  they named: every real regression existed under another name and passed. So the field was
  refused for being absent and accepted for being wrong, and a third form rule could not tell a
  command that selects nothing from one that selects everything.

  `demonstration_proof` runs the criterion as `pytest --collect-only` with an allow-listed argv
  and refuses only on pytest's exit code 5, *no tests collected*; a missing instrument fails
  open rather than refusing an honest plan.

  **Where it refuses is the load-bearing part.** The first cut refused at plan time and was
  wrong: probed against three records filed that morning, two honest plans were refused, because
  at decomposition the test does not exist yet. Plan time now *reports*, in one line naming the
  children whose demonstration collects nothing and saying that this is fine for a test the plan
  will write and a typo otherwise. The **closing** advance refuses, ahead of every side effect,
  on the ground that a record claiming to be done was supposed to have written the test it
  names by now — and the refusal says so, with the two repairs. Measured over this repository's
  backlog when it landed: 17 records carried a demonstration, 1 collected nothing and was open,
  and 0 closed records would have been refused (basicly-u2hl.58).

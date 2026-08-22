- **A gate that refuses a harness git command now names the check and the reason that check
  printed, instead of the argv and the exit code.** Three code paths reported a hook refusal on
  2026-08-21 and none of them named a check. `commit.salvage` reported the *last line of the hook
  chain*, which belongs to whichever hook ran last - in this repository
  `protect-generated-commit`, which had passed - so the one message a reader got pointed at a
  check that did not fail. Two lane closes printed `command failed (1): git commit -m ...` because
  `checkout.run` read `stderr or stdout`: pre-commit writes the whole chain to stdout while `uv`
  writes a `VIRTUAL_ENV` warning to stderr on every run in this tree, so the report was discarded
  and the warning was the entire diagnosis.

  Both streams are now joined and read by structure. A failing hook is located by its verdict
  line, and its reason is taken from *its own* block, so a passing hook's line can no longer be
  quoted as a rejection. Within that block the reason is chosen by what a line claims rather than
  by where it sits: the first design took the block's tail, and real output refuted it, because
  this repository's `pre-commit-script` hook wraps the whole verify suite and its block ends on a
  list of the checks it ran while the answer - `checks failed: 28/32 passed ... (failed: ...)` -
  sits six lines earlier. A failure with no hook chain in it keeps the old wording, since
  `git rev-parse` has no check to name.

  A landing that fails `release-notes` now carries the remedy that gate already printed - the
  exact `changelog.d/<id>.<category>.md` to write - which was being captured and thrown away. When
  a chain did run but names no failing check, the message says so and names
  `.basicly/usage/gate-output.txt`, where the full output is written, rather than implying a cause
  it cannot support.

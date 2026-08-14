- **`tree-growth` is a `[[verify.checks]]` entry: the whole tree's growth is now a number, because
  every other structural gate is blind to it.** `module-size`, `comment-density`, `noqa-debt`,
  `vulture`, `wired-or-deleted`, `lint-imports` and `pyright` are each a per-file or per-symbol
  predicate, so a tree can add fifty individually compliant modules and every one of them stays
  green. That is what happened: `src/basicly/` went from 50 modules holding 408,954 tokens on
  2026-08-07 to 91 holding 476,002 on 2026-08-14, with all seven passing throughout.

  `.scripts/check_tree_growth.py` reports **net tokens over a seven-day window**, in the same unit
  `module-size` counts in, decomposed into what sits in modules that did not exist when the window
  opened, what modules present at both ends did, and what deletion removed. Net tokens rather than
  module count, and the decomposition rather than a mean, because those are the only readings that
  separate growth from redistribution — a module extracted out of another takes from one term what
  it adds to the other, leaving the net flat, while a compliant *addition* moves it by its whole
  size. Chosen against this repository's own history, and the two commits that fixed the choice are
  asserted in `tests/test_check_tree_growth.py`.

  **It reports and never blocks, including when it cannot reach a number.** D23
  (`docs/requirements/factory-loop.md` §15.7) makes a sizing control with no recorded correct firing
  observability; this one has no firing history at all. Its window is anchored on HEAD's own
  committer date rather than the wall clock, so one checkout always answers the same thing, and a
  checkout that does not reach back a week — CI clones the quality-gates matrix at depth 1 — says
  the window is uncovered instead of inventing a baseline.

  Like `module-size` and `noqa-debt`, this is basicly's own gate rather than something `basicly
  install` projects: a consumer's tree is its own decision, and the growth of this one is what the
  number is about (`basicly-5p49`).

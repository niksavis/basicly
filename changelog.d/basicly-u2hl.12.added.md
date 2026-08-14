- **`noqa-debt` is a `[[verify.checks]]` entry: lint suppressions are ratcheted per code and cannot
  grow silently.** `.scripts/check_noqa_debt.py` counts `# noqa` by rule code and fails on an
  increase against the counts frozen in `[tool.noqa_debt]`. Counting is by `tokenize` comment and
  ruff's own directive grammar rather than by substring, so a comment that *looks* like a
  suppression and suppresses nothing is not credited as one.

  It also ratchets `unreasoned_count` in both directions, against a house form of
  `# noqa: CODE — reason`. The argument the gate makes is its own history: the figure was stale
  twice while it was prose, and every suppression it now counts arrived through a green gate
  (`basicly-u2hl.12`).

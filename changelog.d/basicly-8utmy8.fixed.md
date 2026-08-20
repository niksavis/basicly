- **The validator's verdict is read through the markdown an agent actually writes.**
  `validate_gate.verdict_from_reply` stripped whitespace and a pair of enclosing backticks
  and nothing else, so a reply reading `**VALIDATION: PASS**` parsed to no verdict at all —
  and an unreadable verdict costs the whole dispatch, not just the line. Markdown decoration
  is now removed anywhere on the line before the `VALIDATION:` prefix is matched, which
  covers emphasis around the whole line, emphasis around the label alone
  (`**VALIDATION:** PASS`, the shape that puts the markers *between* the prefix and the
  answer, where stripping the ends cannot reach), a heading prefix and a list marker. The
  forms come from agent-written text in this repo's own ledger; single `*` and `__` runs
  around a label line are extrapolated from the same convention and are marked as such at
  `validate_gate._MARKUP`.

  **The refusal is unchanged, and is now pinned.** Only `PASS` or `FAIL` after the prefix is
  a verdict, and only a line that says the prefix is a candidate, so a reply carrying no
  verdict still returns `None` and still queues the decision `basicly-xd79u3` added instead
  of the parse finding a verdict in anything. Two permissive mutations were run against the
  suite to prove that guard is still reachable: dropping the prefix anchor, and accepting any
  non-empty answer, each turn `tests/test_validate_gate.py` red.

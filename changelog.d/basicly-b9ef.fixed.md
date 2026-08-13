- **A claim an epic's own closed children superseded no longer reaches a decider as a current
  fact.** An epic's `## Context` bullets are the whole authority a delegated decision runs on
  (`decider_contract.intake_corpus`), and they are the one part of a bead nothing revisits.
  Measured on `basicly-u2hl` (2026-08-08): four of eight bullets had been superseded by its own
  closed children and one was refuted outright, after which two escalations quoted the refuted
  bullet verbatim, reasoned from it and abstained to a human — while `git merge-tree` reported
  both lanes already mergeable, so both sat in `build` holding live worktrees. The corpus now
  marks every bullet that names no child of its own bead as `UNVERIFIED — possibly superseded`,
  in place at the head of the claim, because a decider reads top to bottom and a correction
  anywhere else is one it never reaches.
- **A bullet is accounted for by naming a child, never by resembling one.** Attribution by text
  similarity was measured against that same case and refused: TF-IDF over the closed children's
  titles ranked the true superseder first for 1 of the 4 known pairs and scored an unsuperseded
  bullet at 0.50 against an unrelated child; term coverage over their full descriptions reached
  2 of 4 with false pairs at 0.78. So nothing guesses which child killed which bullet — a claim
  either names a child of its own bead (the form the hand correction already used, `SHIPPED
  2026-08-08 (basicly-u2hl.4): ...`) or is marked unverified, and anything else is flagged.
- **The `corpus-drift` verify check reports it before a decider ever sees it.** It reads the
  committed tracker export, so it runs in a fresh clone with no tracker binary, and covers open
  parents with at least one closed child. `--strict` names every unaccounted bullet and exits
  non-zero; the wired gate ratchets against `[tool.corpus_drift.frozen]`, which records the one
  bead already unaccounted for when it landed and may only fall (`basicly-b9ef`).

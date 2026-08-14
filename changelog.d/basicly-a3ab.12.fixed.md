- **The projected skill listing fits the budget a consumer actually gets.** A host caps each skill's
  `description` plus `when_to_use` at 1,536 characters and budgets the whole listing at 1% of the
  context window; on overflow it drops descriptions **starting with the least-invoked skills**,
  which is a feedback loop rather than a cost — the skills nobody invokes are the first to become
  uninvokable. The listing had grown past a 2,000-token consumer budget. `catalog lint` now gates
  both caps and the listing is back under (`basicly-a3ab.12`, `basicly-u2hl.45`).

- **`AGENTS.md` is back under its size cap, and the check runs from `basicly check` rather than only
  from `build`.** The audit behind it found the overrun was not the always-on baseline: the extra
  characters are the path-scoped tier that claude and copilot receive as separate rules files and
  Codex, which has no glob-based instruction scoping, must inline. Evicting baseline lines would
  have charged all three families to fix one and left the cause standing, so the Codex cap moved to
  16,000 characters instead. What that trades away is stated where the cap lives: it also stood
  proxy for a vendor claim that adherence degrades with length, which this repo has not measured
  (`basicly-a3ab.1`, `basicly-a3ab.10`).

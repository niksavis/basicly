- **BREAKING: the `code-reviewer` agent is removed; `reviewer` supersedes it.** `code-reviewer` was
  projected into `.claude/agents/` and `.github/agents/` and vendored to consumers by `basicly
  install`, so this deletes an agent you may be invoking by name today. `reviewer` does the same job
  with the stronger contract: it reviews **one named lens** and reports on that axis alone, with a
  severity on every finding and no ranking merged across lenses — a change can pass one axis and
  fail another, and reranking lets the strong axis mask the weak one (`basicly-e2mz.5`).

  **What to do.** Ask for `reviewer` instead, and name the lens you want: `correctness` or
  `security`. Those two are the whole vocabulary. If you name none, it takes one, says which, and
  answers for that axis alone rather than covering both in one reply. It fetches its own diff
  (`git diff HEAD`, or the range or component you name), so the ad-hoc path that `code-reviewer`
  served still works without a VALIDATE dispatch behind it.

  **The old name still resolves.** `roles.resolve_named_role` redirects `code-reviewer` to
  `reviewer` before it checks whether the file is there, so a caller holding the retired name gets
  the replacement rather than a silent fall back to an unspecialised runner. The supersession is
  also stated in `reviewer`'s projected `description`, which is the surface your host matches for
  delegation and lists to you.

  **One thing `basicly install` will not do for you.** Agent projection prunes a projected file only
  when a technology selection excludes its source, so an existing install keeps an orphaned
  `.claude/agents/code-reviewer.md` and `.github/agents/code-reviewer.agent.md` after the upgrade.
  Delete those two files by hand. A fresh install never writes them.

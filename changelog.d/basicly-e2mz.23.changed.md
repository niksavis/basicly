- **BREAKING: the `architect` agent can write, and the one file it may write is the
  architecture document.** `tools` is a vendored consumer surface — `basicly install` projects
  it into `.claude/agents/` and `.github/agents/` — so a consumer who upgrades gets an
  architect whose tool list is `[Read, Grep, Glob, Bash, Write, Edit]` where it was
  `[Read, Grep, Glob, Bash]`. The role named for architecture could previously only ever
  return a backlog about a document somebody else had to write.

  **The narrower constraint is now in the role's instructions rather than in its tool list**,
  and that is the part to read before upgrading: the agent is told it writes exactly one file,
  the architecture document, and is read-only everywhere else, but nothing in the projected
  `tools` allowlist enforces the *which file* half. If your repository depended on this being
  one of the agents that mechanically cannot edit, that is what changed. The trade-off is
  recorded rather than hidden: a document author differs from a tree surveyor in both tools and
  artifact, so it could have been an eighth role, and widening this one was the choice taken
  (basicly-e2mz.23).

- **A lane that adds a config key can now declare it in the same commit.** A repo's
  `basicly.toml` is validated against the `CONFIG_SCHEMA` that repo's *own tree* ships, not
  against the schema of whichever engine happens to be running. This unblocks the landing:
  `basicly loop advance` runs from the base checkout, so the process validating a lane's config
  is the pre-merge engine, and a single commit that taught `CONFIG_SCHEMA` a name and declared
  it was refused for a key introduced by the code one line away — four times in the field
  (`[worktree] append_only_paths`, `[runner] quiet_after`, `[tracker] mode`,
  `[catalog] rank1_floor`), each time dying before verify ran with a message that read as a
  config typo. The tree's schema is read statically, so nothing imports an unmerged engine, and
  it fails closed: a tree whose schema this reader cannot parse falls back to the running
  engine's and the refusal then names the ordering rule (schema first, declaration next) rather
  than leaving the operator to work it out. The strict refusal itself is unchanged — a checkout
  with no schema change is judged by exactly the schema it was before, and a consumer repo,
  which ships no engine source, is unaffected (basicly-69az).

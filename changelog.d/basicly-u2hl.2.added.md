- **Every unit of work is assigned an integrity level, by a deterministic rule over its declared
  paths.** Three levels, and the level — not a judgement and not a prompt — selects the gate set,
  the model tier and the rework allowance a package earns, read from one record rather than
  re-derived per caller. L3 is the five consumer surfaces the semver freeze names (the CLI,
  `basicly.toml` and its overlay, the catalog source schemas, the generated-file/manifest
  contract, the owned ledger format); L1 is docs and tests; everything else, including a path the
  rule has never been taught, is L2. The rule is total and single-valued: every path a repo can
  hold resolves, and no path is claimed by two clauses. Because a path rule alone over-classifies,
  a change to a consumer surface that is under the configured line threshold and alters no public
  signature is downgraded to L2 with the reason recorded. Classify assigns the level from the
  scope it is given and records it as a `[harness-classification]` comment, so the verdict travels
  with a clone (basicly-u2hl.2).

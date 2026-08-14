- **VALIDATE now dispatches the `reviewer` agent, once per lens, beside the validator.**
  The agent was authored, projected to both agent roots and vendored to consumers, but
  `roles.ROLE_BY_PHASE` mapped a phase to exactly one role, so nothing could reach it.
  A phase now resolves through two tables: `ROLE_BY_PHASE` for the role that drives it,
  and `LENS_ROLE_BY_PHASE` for the role it fans out over `roles.REVIEW_LENSES`. Each
  review is dispatched with its own lens in the brief and records its findings under its
  own `[harness-review] lens=<lens>` marker on the unit; nothing merges two lenses into
  one ranking. The vocabulary ships as two axes — `correctness` and `security` — so an
  L3 unit pays two extra read-priced dispatches per VALIDATE advance, and L1 and L2 units
  pay nothing because they never derive the phase (`basicly-feje`).

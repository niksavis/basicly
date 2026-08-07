- **Catalog routing is gated deterministically, at zero token cost.** `basicly catalog lint`
  now ranks every model-invoked entry's description with a stemmed TF-IDF ranker (pure Python,
  no new dependency, no embeddings) and enforces three assertions: a positive prompt ranks its
  owning entry in the top-k, a negative prompt is outranked by the different entry it declares
  as `owner`, and no two descriptions exceed a pairwise similarity ceiling (error at 75%,
  warning at 50%). The evidence is a per-entry `evals.yaml` colocated with the catalog source
  and never projected into a skill root. The CI metric is the **rank-1 rate**, printed on every
  run and checked against `[catalog] rank1_floor` in `basicly.toml`; a companion
  `rank1_floor_high_water` ratchets that floor so it can be raised but never lowered, because
  lowering a floor to make a regression pass is deleting the test while looking like
  maintenance. A prompt that scores zero fails instead of passing on a tie-break, so an
  assertion cannot report coverage it never had. Authoring the corpus found five descriptions
  missing vocabulary users actually say — `tool-fd`, `tool-ripgrep`, `tool-sd`, `tool-typos`
  and `tool-uv` — and one stemmer defect that stopped "what branch am I on" reaching `tool-git`
  (basicly-m4zv.2).

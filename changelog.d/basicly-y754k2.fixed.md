- **The board producer is four modules where it was two, so a further board unit fits.**
  `board_fields` had 18 tokens of size headroom and `board_snapshot` 23, and units C, D and E
  of the board all consume them, so none of them could be built. The split line was already
  nameable rather than arbitrary and it is the one the record named: what may cross the wire
  against which rows a section is, and then which sources a section reads against the document
  that assembles them.

  | module | tokens | headroom | holds |
  | --- | --- | --- | --- |
  | `board_fields` | 1607 | 18 → 2393 | the bounds, and a marker as fields |
  | `board_sections` | 2846 | new → 1154 | the six row reducers, and `LaneFacts` |
  | `board_snapshot` | 2845 | 23 → 1155 | the ledger half, and `build_document` |
  | `board_usage` | 1324 | new → 2676 | the `.basicly/usage/` sections |

  Neither seam imports back: a reducer needs the bounds and the bounds need no reducer; the
  assembler needs the usage sections and they need no assembler. Both new modules got their own
  tier in `.importlinter` under the `exhaustive` contract, so a maintainer decided where each
  sits rather than the gate inferring it, and `lint-imports` reports 2 kept and 0 broken. The
  architecture's layering block is **regenerated** rather than edited, as the record required:
  39 tiers to 41, 105 modules to 107. The test modules were split to match, which
  `check_test_naming` then required rather than suggested - it refused `board_usage` for having
  no test file named after it, which is the drift that gate exists to stop.

  **One of this record's acceptance criteria is refuted, with the measurement.** It asked for no
  new density waiver. Splitting a module raises the prose share of *both* halves by
  construction: the code divides and the contract docstring does not. `board_snapshot` lost 630
  tokens of code and 505 of prose in one edit, so it got smaller and denser at once - 3980 to
  2845 tokens, 47% to 51.5%. Every cheaper reduction was taken first and measured at each step,
  reaching 51.5% from 55.7% and 55.1%: prose moved to the module whose code it describes, two
  stale cross-references the split itself created were repointed rather than kept, and two
  restatements of what a test asserts were dropped. What remains is measurements - the 93-fold
  6.1 s cost of `observe()`, the `supervise -> board_snapshot -> supervise` cycle, the 140/203/1
  ask pairing - and a ruff `D`-mandated `Args:` block that is a third of what is left. Two
  stated waivers were taken instead of deleting those, and no rebalancing avoids them: folding
  `board_usage` back gives 4169 tokens against the 4000 cap, and extracting the three
  caller-supplied facts records instead gives a module that is 89% prose (basicly-y754k2).

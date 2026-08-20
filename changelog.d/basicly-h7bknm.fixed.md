- **The architecture layering section's tier and band counts are generated from the import
  contract.** Section 34 stated how many tiers the engine has and how many modules they hold,
  and **nothing read any of it** — no script, no test; `docs-claims` asserted CLI coverage
  only, and `code-citations` checks that a citation reaches a heading, not that a number
  inside a document matches a config file. Measured against `.importlinter` on 2026-08-20 the
  document said 36 tiers where the contract had 37, and its band labels summed to 98 modules
  where the contract had 102. A previous lane corrected the three numbers its own change
  moved and deliberately left two band figures, because band boundaries are read off a
  diagram nothing binds — so correcting them could itself be wrong.

  Correcting a number is the repair that is wrong again on the next tier, so the whole block
  is now a `docs-claims` generated block over `.importlinter`: the tier count, the module
  count, every band's module count and the diagram's declared-exemption edges are all derived,
  and a tier added to the contract fails the gate until the block is regenerated. **The band
  boundaries are the declared half and the block says so where a reader meets the counts.**
  Nine bands over 38 tiers is an editorial reading the contract does not carry, so each
  boundary and each band's example modules are declared in `.scripts/docs_claim_layers.py` and
  the counts are derived against them — a boundary the contract no longer declares, an example
  module that moved band, or a tier below the bottom band all raise rather than render, because
  a band count nobody can derive printed as though it were derived is worse than a wrong one.

  The two figures the previous lane left are now derived rather than guessed: band 7 holds 13
  modules and band 9 holds 26, and the labels sum to the contract's 104 — cross-derived
  against the package's 103 top-level modules plus the `renderers` package (basicly-h7bknm).

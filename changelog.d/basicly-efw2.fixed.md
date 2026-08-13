- **Completing a bead's `## Scope` no longer makes its lane look bigger.** One field was serving
  two gates that want opposite things: the merge scope-collision gate wants the declaration
  complete — every path the diff touches — while the sizing band wants it small, because it prices
  what the declaration *reads*. Declaring honestly for the first necessarily inflated the second.
  Measured inside a single landing on `basicly-u2hl.14`: 13 scope entries estimated 78,709 and the
  merge refused 16 undeclared paths; 27 entries estimated 197,646; 35 entries estimated 245,466 —
  and the diff was exactly as wide throughout. Only the declaration moved, and the working-set
  ceiling was raised twice to let it through, which is a ratchet moved by an artifact rather than
  by evidence. A bead may now declare a `## Working Set` section — the subset of globs the lane
  must actually read, written as backticked globs like `## Scope` — and the band, the dispatch
  forecast and the ceiling derivation price that instead. `## Scope` stays the ownership
  declaration the merge gate reads and the collision graph learns from, complete and free. A bead
  that declares no working set is priced from its scope exactly as before, so nothing already
  authored changes; a ceiling refusal now names declaring one as the alternative to splitting a
  lane that has not grown (basicly-efw2).

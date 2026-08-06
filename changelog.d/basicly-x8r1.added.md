- **A new shipped skill, `interface-facts`, makes a third-party fact something you fetch
  rather than recall.** Before writing code, a design note, or any claim that depends on how
  an external interface behaves — a CLI flag, an API field, a model id, a price, a limit, a
  version — the skill has you establish it against the vendor's current documentation, and it
  applies the same standard to a claim a repo document already asserts.

  It exists because a recalled interface fact reads exactly like a verified one. A design
  document stated that one supported agent CLI reported no token counts, four hundred lines
  above its own section documenting the extraction mechanism and the runner code that
  implements it; the stale summary was read in preference to the section it cited and repeated
  to the owner as fact. Nothing in the harness could have caught that, because every gate the
  repo runs checks structure or behaviour — not whether a sentence about somebody else's tool
  is still true (`basicly-x8r1`).

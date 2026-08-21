- **`basicly board --out <path>` writes the board as one self-contained HTML file.**
  The producer that folded the snapshot had no caller outside tests, so no snapshot
  could be produced and nothing rendered. The command now emits the page and the
  `harness-board/v1` snapshot beside it, and prints a per-source inventory naming
  each source it read. Every panel renders its own `generated_at` and a computed
  age, and a section the producer did not emit renders as `not emitted by this
  producer` rather than as a zero (`basicly-rn0o.3`).

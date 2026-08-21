- **The snapshot contract is checked against a producer that is not basicly.** A
  fixture emits a `harness-board/v1` document without importing the engine, and the
  validator admits it, so the published contract is exercised as a foreign consumer
  would exercise it rather than only against our own producer (`basicly-rn0o.13`).

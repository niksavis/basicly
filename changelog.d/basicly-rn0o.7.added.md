- **The supervisor tick writes a board snapshot.** A supervised pass now publishes
  `.basicly/usage/board/snapshot.json` on its own tick, temp-then-rename, so a
  reader sees the previous document or the new one and never a partial. A failure
  logs one line and never fails the pass (`basicly-rn0o.7`).

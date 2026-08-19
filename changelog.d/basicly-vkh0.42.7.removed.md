- **The external tracker binary and its store are gone from the runtime path entirely.**
  The cutover ladder collapsed to its last rung: `[tracker] mode` now accepts exactly one
  value, `owned`, and the `external` and `dual` modes are gone with the store they named.
  The engine reads and writes the owned append-only event ledger under `.basicly/ledger/`
  and nothing else. The `.beads/` directory, its ignore rules, the binary's installer, its
  tool skill and the five skills that named it are all removed, and the commit gate reads
  the owned ledger instead. **What this means for a consumer:** installing basicly no
  longer installs, pins or upgrades a third-party tracker binary, and no command shells out
  to one. The `mode` key itself is kept rather than deleted from the schema, so a repository
  that already committed `mode = "owned"` is not refused as declaring an unknown name
  (basicly-vkh0.42.7).

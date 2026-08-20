- **The landing scope gate no longer faults a lane for the two files every lane writes.**
  This repo's conventions have each lane record its ratchet delta as `basicly.d/<id>.toml` and
  its release note as `changelog.d/<id>.<category>.md`, so neither appears in any bead's
  `## Scope` and `loop._scope_block` reported both as out-of-scope edits. Observed on
  `basicly-gvlpxm`: the two false entries arrived in the same message as one genuine
  collision, under a closing line that offers `[policy] scope_collision = "warn"` as the way
  to land — so the noise argued for turning the gate off.

  Both are now in scope by construction, derived from the record id the engine already holds
  (`config.lane_scope`). Derived and not a directory whitelist, which is the whole point:
  `basicly.d/<other-id>.toml` is a real collision and this gate is still the only thing that
  sees it, while `README.md` in either directory names no record and stays undeclared
  (`basicly-kjc5.64`).

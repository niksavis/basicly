- **A published snapshot contract, `harness-board/v1`, and `basicly board validate` to check
  a snapshot against it.** The schema ships as a catalog source at
  `.basicly/core/schemas/board-snapshot.schema.json`, and a `board-schema` entry in the
  verify pipeline checks it on every run. A snapshot carries only `meta` as required; every
  other section is optional, so a producer declares what it can supply rather than filling
  fields it cannot know. Closed value sets are deliberately few — `phase`, `status`, `type`
  and edge kind are open strings that name this project's values as examples rather than as
  the enum, so a producer with its own vocabulary is not refused. **What this means for a
  consumer:** the contract is the interface, so a repository can emit a conforming snapshot
  from whatever work tracker it already uses and have it checked, without adopting this
  project's store (basicly-rn0o.1).

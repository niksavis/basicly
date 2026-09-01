- **The contributor setup block now activates every git gate.** It named
  `pre-commit install`, which rewrites the pre-push hook without the ledger guard, so a
  fresh clone that followed it verbatim still had `basicly hooks-check` reporting
  pre-push as not installed. It now names `basicly hooks-build` (basicly-7owkkz).

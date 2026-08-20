- **The harness board's design now makes the `harness-board/v1` snapshot the only interface, and
  the contract readable without basicly.** `docs/requirements/harness-board.md` is revised so every
  consumer reads a snapshot document and nothing else, basicly's producer is one implementation
  rather than the definition, and a foreign producer gets a stated six-clause adapter contract plus
  a conformance kit that proves it. Two success claims were measured false and are fixed with a
  named remedy: `basicly board validate` answers `not-installed` and exits 1 in a directory with no
  catalog, so the check that was supposed to prove independence *was* the runtime, and the contract
  was distributed only to repositories that had already installed it. The remedy is a standalone
  single-file conformance script under `.basicly/core/kit/board/`, and the snapshot schema freezes
  under its own `harness-board/vN` version rather than folding into basicly's semver. Wall mode and
  the action surface are back in scope with the superseded four-unit decision recorded rather than
  deleted, the conformance kit moves from last to second in the build order, and the marker-family
  set and the wall's idle state are settled by measurement. **What this means for a consumer:** a
  repository that never runs `basicly install` can emit a conforming snapshot from whatever work
  tracker it already has, check it under a bare `python3`, and get a working board
  (`basicly-rn0o.10`).

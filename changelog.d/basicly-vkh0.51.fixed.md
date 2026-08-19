- **The suite's live-ledger guard no longer undoes the write it catches.** It read the
  committed event log's bytes around every test and, on any difference, wrote the old
  bytes back and failed the test in flight — a hand edit to an append-only log, racing a
  writer holding the ledger lock the test process never takes. Measured 2026-08-19 in the
  base checkout: twenty `basicly tracker write` calls issued while `pytest` ran produced
  zero events, and the same twenty in a quiet tree landed twenty of twenty. The guard now
  attributes a change through a PEP 578 `open` audit hook, which sees every route
  including a kit loaded by path: a write from the test process fails that test and names
  it, a change from another process is reported once per path as unattributed, and in
  neither case is a byte restored (`basicly-vkh0.51`).

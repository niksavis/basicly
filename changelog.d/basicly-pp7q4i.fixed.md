- **A handoff artifact is now a typed `artifact` event, so the transport no longer cuts the
  body its consumer reads.** An artifact travelled as a `[harness-artifact]` comment marker,
  which put the JSON body in a `text` payload key — free text the per-event cap cuts at 4096
  bytes. JSON cut mid-token is not JSON, so the producer validated one payload and the
  consumer was handed a different one: measured over this repository's ledger on 2026-08-18,
  31 of the 54 artifacts ever written are stored cut, 337,353 bytes are gone, and all 23
  surviving truncated record-and-kind pairs are refused by their own entry predicate, against
  a control of intact ones that are admitted. `basicly.tracker.add_artifact` now appends one
  `artifact` event carrying the kind as a typed field and the body under `body`, a key
  `events.FOLD_READ_KEYS` names and the cap may never reach, and the read resolves that event
  first. A 22,621-byte plan that came back as a cut string through the marker now reads back
  byte-identical. **The retired marker stays readable**: its rows are on an append-only log
  and the cut bodies cannot be recovered, so a unit carrying only a marker still resolves to
  the artifact it holds and is refused naming the truncation and both byte counts rather than
  read as carrying nothing (`basicly-pp7q4i`).

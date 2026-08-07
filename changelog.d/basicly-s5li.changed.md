- **The harness's `[harness-*]` markers are carried as owned-ledger events instead of `br`
  comments.** Step 5 of `docs/design/work-tracker.md` §5, and the step that actually removes
  `br` from the engine rather than merely making it non-authoritative. `comments` was the
  largest remaining dependency — 26 of the engine's 55 `br` call sites and 45% of all recorded
  tracker traffic — and measured over the live tracker, 1646 of its 1834 comments (89%) are
  harness markers using a beads comment purely as transport: checkpoint approvals, gate
  records, grants, rework counters, needs-input, the human-wait clock, dispatch records and
  spend rollups. Three new seams carry them — `br.add_comment`/`br.try_add_comment` to write,
  `br.read_comments`/`br.try_read_comments` to read one bead, `br.all_comment_texts` for the
  whole-tracker evidence read — and with `[tracker] mode = "owned"` each answers out of the
  event log under `.basicly/ledger/` with no `br` spawned at all. The two contracts are
  deliberately split: a counter or a refusal reads the hard function, which raises when the
  store cannot answer rather than reporting "no markers" and letting the loop advance past the
  gate the marker existed to hold, while an idempotency or telemetry read takes the soft one.
  The read-only ban that a pre-flight gate runs under is now enforced at the seam itself, so it
  still refuses a marker write on the rung where there is no `br` call underneath to inherit it
  from. Below `owned` nothing changes: the write still goes to `br` and the dual write still
  mirrors it. The 188 human comments are untouched — a human writing prose runs `br` directly
  and the engine never spawns that. Two `comments list` spawns remain at their own call site
  (`decompose`'s sizing markers, `supervise`'s found-info records), each writing and reading
  the same store; retiring them is `basicly-wpc8` (basicly-s5li).

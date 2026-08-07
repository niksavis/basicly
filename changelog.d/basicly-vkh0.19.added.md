- **`[tracker] mode` puts the br seam on a rung of the work-tracker cutover: `external`,
  `dual`, or `owned`.** `dual` mirrors every write the engine makes through
  `basicly.br` onto the kit's owned event log under `.basicly/ledger/`, with `br` still
  authoritative for reads. `owned` flips `br.read_record` — the one record-read seam — to
  answer out of that log, while `br` is still written, because the other ten subcommands
  the engine spawns still read out of it. Repos that declare nothing keep the pre-cutover
  behaviour exactly: no ledger is created and the kit is never loaded.

  A write the ledger cannot record **fails the command** rather than warning. Two stores
  are only worth running side by side while they hold the same facts, and the moment a
  missing mirror is cheap to fix is before the next write lands on top of it — so a br
  write with no owned-ledger translation, an `update` flag with no mapped field, and a
  ledger that refuses the append are all errors at the call site.

  The dual write is also the writer `differential.KIND_GATE` was defined for. The JSONL
  export carries no gate field at all, so the import step had nothing to load and the
  shadow differential reported *inconclusive* on the gate query for every population it
  could build — clean, and unable to say that clean meant anything. With `gate report`
  mirrored, a run over a population built through the seam comes back clean **and**
  conclusive, which is the condition `docs/design/work-tracker.md` §5 step 4 licenses the
  flip on (`basicly-vkh0.19`).

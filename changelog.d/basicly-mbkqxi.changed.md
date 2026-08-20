- **The event log's durability bound is measured rather than asserted, and the defect it was
  filed for does not exist.** `events.py` states the choice - no `fsync`, the push is the
  durability boundary - and nothing exercised it, so the bound behind that sentence was a
  claim. It is now five tests. What they establish:

  The append path asks the platform for no sync at all: a spy over a whole `append()` of five
  drafts counts zero `fsync` and zero `fdatasync`, against a control that counts one on a
  deliberate sync, and a tokenized scan excluding comments and strings finds no sync call on
  the write path - so the module docstring's own sentence cannot satisfy the probe. An
  interrupted batch is a **whole-line prefix**, never a hole and never a tear: a batch under
  one buffer chunk is all-or-nothing, and a larger one loses a suffix of complete lines that
  folds with no fork and no quarantine.

  **That refutes the record this was filed under.** `basicly-vkh0.30` holds sequences 1-8 and
  10-34, and the two survivors around the gap are **one file line and 525 microseconds apart**,
  while one append over that ledger measures 54-61 ms - so they were minted in one batch and
  written by one call. A partial batch truncates a suffix, so an interior line whose successor
  survives cannot be lost that way, and **an `fsync` would not have prevented it**. The loss
  happened after the bytes were in the file. The class moves from an unflushed write to a
  post-write mutation; the cause stays unidentified, as the record said.

  The useful half is that the next one is already detectable and is now pinned. The event above
  a hole carries totals a fold of the survivors cannot reach, and the next append restates from
  the fold, so exactly one event disagrees - measured live as one disagreement across 6,263
  events, one sequence hole, nothing quarantined. `BUFFER_CHUNK_BYTES` is asserted against
  `io.DEFAULT_BUFFER_SIZE`, having had exactly one occurrence in the tree before this: its own
  definition (basicly-mbkqxi).

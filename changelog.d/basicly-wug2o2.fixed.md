- **A handoff artifact the event cap cut is reported as truncated, with both byte counts,
  instead of as a malformed artifact.** The entry predicate refused a stored artifact that had
  been cut at 4,096 bytes with a schema type violation on the top-level instance and a
  300-character fragment of the cut JSON, so a transport that destroyed a valid artifact read as
  a producer that wrote a broken one — and a blocked record's own status surface named a
  different, coexisting cause, so nothing anywhere named the real one. The reason now names the
  cut, the stored length, the original length, and re-recording from the producing state as the
  remedy, because the body cannot be recovered from an append-only log.

  Measured across the whole ledger when it landed [2026-08-18]: 23 record-and-kind pairs held
  cut artifacts, 23 of 23 name truncation, 24 of 24 uncut pairs are still admitted, and nothing
  is falsely called truncated — an artifact that is malformed but whole reports its schema
  violations unchanged. The flag had never reached the reader: the row projection reduced every
  payload to text and a stamp, discarding the two truncation keys one seam below the predicate.
  They are now carried both-or-neither, which is a real constraint rather than tidiness, because
  the naive carry emitted a flag with a null length.

  **This does not stop the truncation**, only the misreporting of it (basicly-wug2o2).

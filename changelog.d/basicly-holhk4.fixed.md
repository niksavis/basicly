- **A tracker write that translates to no event at all is refused, instead of reported as
  `recorded:`.** `cmd_write` printed its confirmation from the fact that no exception had been
  raised rather than from what landed, and `mirror._update_drafts` returns an empty draft list
  when an `update` carries no field flag. So `basicly tracker write -- update <id>` printed
  `recorded: update <id>`, exited zero, and appended nothing - identically whether or not the
  record existed, so the message did not discriminate either. Verified both ways.

  **The refusal went one level up from the verb, and that is the point.** The record scoped it to
  `mirror._update_drafts`; it lives in `owned_write.append`, which sees the drafts every one of
  the seven translated verbs produces. The defect is the shape rather than the verb - any
  translation yielding nothing is a confirmation about nothing - and `basicly-vkh0.50` already
  owns that general claim. `init` and `sync` are exempt by construction: `mirror.UNMIRRORED_WRITES`
  is the set of writes that legitimately state nothing about a record, and it is the same set the
  untranslatable-write refusal already names, so it went from private to public rather than being
  respelled.

  The order of the two refusals is forced rather than chosen. `refuse_a_write_to_an_absent_record`
  returns early on an empty draft list, by design, so it cannot speak for a flagless write at all;
  the records-nothing check has to run first. A flagless update naming an id nothing holds
  therefore reports what it would have changed rather than that the id is unknown. A flagged
  update naming that id still gets the absent-record message, which is the case where the id is
  the thing to fix.

  This also corrects `basicly-6oypkd`'s premise, which that record already states: its
  "nothing reaches the ledger for an absent record" was verified with a flagless probe, so the
  zero came from this defect and not from the one being reported. Both halves are pinned by a
  test shown to fail when its half is reverted, and the record's four-step reproduction was run
  against the live CLI: flagless refuses, flagless on an unknown id refuses, and `update <id> -p 1`
  still prints `recorded:` (basicly-holhk4).

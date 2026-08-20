- **An artifact recorded against a record the ledger does not hold is refused, naming the id,
  instead of appended.** `basicly-6oypkd` landed the absent-record guard in
  `owned_write.append`, which every argv-shaped write reaches. `tracker.add_artifact` does not
  reach it: it hands the ledger an object rather than an argv, precisely so a JSON body is not
  flattened into the free text the per-event cap cuts. So the guard covered six verbs and one
  surface sat outside it, which is the shape where a control reads as complete and is not. The
  guard is now one definition with two callers rather than a copy, and `add_artifact` holds the
  ledger lock across the check and the append for the reason `append` already did: the record
  set a write is refused against has to be the set the append lands on.

  **The consequence was narrower than the record claimed, and the correction is worth having.**
  The record said an artifact against a mistyped id is "evidence attached to nothing". Measured:
  the fold *does* mint the id, as a record with no `created` event, so `ledger-bodies` reports it
  at the next commit. The hole was covered downstream. Refusing at the seam is still the right
  place, because an append-only log has no undelete and the event is already written by the time
  that gate speaks - but a reader should know a gate would have shouted.

  **26 existing tests depended on the tolerance**, across three modules, every one writing an
  artifact against a record its fixture never created. Each is fixed by opening the record, the
  way `basicly-6oypkd` fixed the same shape in `test_gate_source._repo`, rather than by loosening
  an assertion. The two autouse fixtures key off `request.fixturenames` instead of taking
  `work_repo`, so the tracked-tree copy is not built for the tests that only want `tmp_path`.

  **One module had to give up tests for this to land, and the reason is the finding.**
  `tests/test_handoff.py` needed 190 tokens and was frozen at 6302 having already been
  rebaselined three times - 3986 to 4134 to 5124 to 6302, +58% - with all three fragments giving
  the same reason and deferring the same extraction. That extraction has since landed:
  `cut_violation` lives in `artifact_record`. The test-side home they named,
  `test_handoff_schemas.py`, has the room and is the wrong responsibility: it validates schema
  files, takes no repo fixture and never calls `handoff.record`. The corrupted-artifact section
  moved instead to `test_handoff_states.py`, where the entry-refusal tests it joins already live.
  `test_handoff.py` fell to 5784 for the first time across those three concessions
  (basicly-kmqno2).

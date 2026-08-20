- **A repair that re-lands from validate records the `change-summary` for the landing it just
  performed.** The landing path has two merge call sites and only one recorded.
  `loop._verify_and_land` merges and calls `_record_change_summary`, which after
  `basicly-gvlpxm` correctly records the head the merge took; `loop._repair_from_validate`
  called `merge.merge_worktree` directly and recorded nothing. So after any repair re-land the
  artifact still described the first landing - `gvlpxm`'s own defect statement word for word,
  reached by the other route. `gvlpxm`'s own repair demonstrated it: its summary still names
  `d3422f81`, the pre-rebase head, while the fix reached main as `7381a145`.

  The changed paths are read **before** the merge, which is the ordering `_changed_paths`'
  docstring requires - afterwards the changed set is whatever else landed alongside - and a
  failed merge records nothing, leaving a true summary of the first landing rather than
  replacing it with a summary of nothing.

  **The record posed a design question and declined to answer it; its acceptance criteria
  answer it.** The two readings were that a re-land should re-record, or that the phase model
  is wrong to let a merge happen outside the state owning the artifact. The criteria choose the
  first, and it holds on its own terms: `handoff.record` is content addressed rather than write
  once, so the corrected payload is a second event and nothing is overwritten.

  **Nothing exercised this path at all.** A search of `tests/` for `_repair_from_validate`'s
  own block message returns nothing, against a positive control finding the string in
  `loop.py` - which is why the missing call went unnoticed through two records about the same
  artifact. The first test pair written for it did not discriminate either: reading the paths
  *after* the merge still passed, because the fixture pinned `branch_changed_paths` to a
  constant. The changed set now differs across the merge by injection, so *when* it is read is
  observable, and that mutation fails.

  `tests/test_handoff_states.py` crossed the 4,000-token cap under the new pair, carrying a
  third responsibility by then. The entry-refusal tests moved to `tests/test_handoff_entry.py`,
  their second move for the same five tests - which is `basicly-e2r08j`'s mechanism exactly:
  every split raises both halves' prose share, so the module that receives a section is the
  next one to overflow (basicly-3katht).

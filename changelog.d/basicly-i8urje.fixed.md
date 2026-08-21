- **`basicly worktree create` records the binding it just earned.** The verb provisioned the
  tree, the branch, the dependencies and the hooks and wrote no worktree binding, so
  `loop_state.derive_phase` read the record as `intake` and no advance could land a merge that
  had already happened. Three records in one session were closed by hand for it, each carrying
  a prose close reason where a `release-record` artifact should be (`basicly-i8urje`).

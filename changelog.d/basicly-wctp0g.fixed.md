- **The session spend the board shows now advances while a lane runs.** It read `0%` for a
  whole pass because the recorded figure only moves when a run record lands; `spent_tokens_live`
  adds each running lane's live-reported tokens, marked as an over-estimate against the recorded
  spend, which the D3 grant gate still binds on unchanged.

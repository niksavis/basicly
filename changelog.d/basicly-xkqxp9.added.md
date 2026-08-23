- A `pipe-status-guard` PreToolUse hook refuses reading a pipeline's exit status when a
  pass-through filter ends it (`cmd | tail` reports tail's status over a failed gate);
  it fires only where the status is actually read and names the redirect-to-a-file repair.
- `falsify-first` gains the rule that a probe must exclude the file defining its own
  vocabulary: the instrument is not a member of its own population.

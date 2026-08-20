- **The reader that reports a truncated handoff artifact now lives with the recorded form,
  and `handoff.py` is back inside the module size cap with no frozen baseline.** The
  cut-violation lookup moved from `basicly.handoff._cut_violation` to
  `basicly.artifact_record.cut_violation`: reading the retired `[harness-artifact]` marker off
  a stored row is the recorded form's job, and the ruling only needs the reason it hands back.
  Behaviour is unchanged — a body the per-event text cap cut is still refused naming the
  truncation and both byte counts, rather than reported as a schema violation. `handoff.py`
  falls 4504 -> 3946 tokens under the 4000-token cap, so the baseline `basicly-u2hl.59` froze
  it at is deleted rather than left licensing regrowth, and its prose share is unchanged at
  65.4% because the extracted unit was within a tenth of a point of the module's own share
  (`basicly-09lc5o`).

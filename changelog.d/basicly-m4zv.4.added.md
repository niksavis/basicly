- **A judged finding must carry a severity, and a reviewer bundle may not pre-judge the review.**
  Two deterministic checks, both free at CI time. A judged rubric check answering `no` is now a
  finding that must classify itself `BLOCKER` / `IMPORTANT` / `MINOR`; one that does not is
  rejected as a schema violation and re-requested once with the violation named, exactly as an
  unparseable reply would be, rather than accepted as a dispute nobody can triage. The invariant
  sits on the verdict record itself, so there is no path — parse, construct, or report — to a
  severity-less judged finding, and the severity rides onto both the `rubric-judged` gate note and
  the queued validate decision. Separately, every reviewer bundle the engine assembles (the
  semantic-review prompt and the rubric judge prompt) is linted for finding-suppressing directives
  — "do not flag", "don't treat X as a defect", "at most Minor", "the plan chose" — and refused
  rather than emitted weakened; the lint covers the material under review as well as the task
  text, because a reviewer reads one prompt and cannot tell instruction from evidence
  (basicly-m4zv.4).

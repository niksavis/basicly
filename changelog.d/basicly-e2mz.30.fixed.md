- **A tracker `update` through the engine seam writes every field the store holds under its own
  key, instead of refusing all but three.** The translator was three flags wide — `-t`,
  `--type` and `--external-ref` — so a write of a description or of acceptance criteria was
  refused outright, and a record filed through the seam could carry a type and an external ref
  and nothing else. The refusal itself was right, because dropping the field silently is the
  divergence that layer exists to prevent; the flag table was the defect. Fifteen flags now
  translate, naming ten fields — title, description, design, acceptance criteria, notes, type,
  priority, assignee, owner and external ref — with `--body`, `--acceptance` and `-d` taken as
  the aliases the store itself accepts. The field *names* matter because the folded record
  renders them straight back, and the plan gate reads `acceptance_criteria` off that record.

  Priority goes through a converter rather than `int`, so `-p P1`, `-p p1` and `-p 1` are one
  priority and the ledger holds the integer; the same table serves `create`, which used to
  crash on `-p P1` with a bare `ValueError`. Four flag families stay refused, each for a
  measured reason, and the message now states the precondition **before** naming the repair so
  that following it cannot turn an append into a replace: the label flags accumulate against
  the set a record already holds rather than replacing it, `--claim` carries no value, `--due`
  and `--defer` are re-based against the host clock, and `--estimate` lands under a field no
  record here holds (basicly-e2mz.30).

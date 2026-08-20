- **Two live code citations now point at documents that exist and sections that define what
  the citing line claims.** Both were inside the `code-citations` gate's frozen debt on the
  day it landed, so they were recorded rather than blocking, and both were real stale
  pointers. `tests/test_policy.py` cited `gates-and-rework-design.md` sections 1 and 2 — the
  gate taxonomy and the rule that a pre-flight gate is read-only — and **that document was
  absorbed and deleted on 2026-08-08**, which no reader would notice without trying to open
  it. Its content went to `factory-loop.md` §5.1, which says so in its own first line, and
  `policy.preflight_gate` already cited that section for the same rule; the test now cites it
  too. The second citation named §1.1 and §4.1, whose mappings landed in the same section —
  and the rubric split that §4.1 argued has no document of its own, which the docstring now
  states rather than implying a section number for it.

  The shipped tracker kit's `events.py` cited sections 32.10, 32.3 and 32.3.2 while its module
  header binds the kit to `work-tracker.md`, whose highest section is 16: it named one document
  and meant another, which is exactly the ambiguity the gate exists to expose. Those three are
  **architecture** sections — the per-event size cap, the event vocabulary, and the reader's
  alias table — and each citing line now names `architecture.md`. Naming the document per line
  is the repair rather than a path binding, which would have re-attributed every one of that
  module's bare marks to the architecture when most of them really do mean `work-tracker.md`.

  Neither reference was deleted to pass: an unresolved mark is a pointer whose target moved,
  and the pointer is the evidence of what the code was reasoning about. Both modules reached
  zero unresolved marks and **both frozen entries were deleted in the same diff**, so the
  closed list now refuses a single new one in either file (basicly-fsuhg3).

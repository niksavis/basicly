- **Opaque record ids sized against a declared collision probability, and derived ids for
  evidence.** `.basicly/core/kit/tracker/ids.py` mints a record id from an explicit
  collision budget rather than from a guess at how long is long enough: the length follows
  from the birthday paradox against a stated maximum probability, and it is **adaptive** —
  which is safe precisely because existing ids never change.

  An id is opaque. Nothing in the harness may parse one to recover meaning from its text,
  because an id whose characters carry information is an id that cannot be reissued, and a
  prefix-anchored gate has already truncated a slug-shaped one.

  Evidence ids are **derived from content** instead of minted, so the same fact recorded
  twice is the same id and an idempotent write is idempotent by construction rather than by
  a caller remembering to check (`basicly-vkh0.12`).

- **The append-only event log the owned tracker is built on.**
  `.basicly/core/kit/tracker/events.py` is the store the rest of the kit derives from: a
  record's state is a **fold over its events**, so history lives in the data rather than
  depending on git history surviving a squash or a shallow clone, and everything else — the
  snapshot, any index — is derived and disposable.

  The fold is a function of the event **set**, not of file order: a shuffled log, a
  concatenated one, or the same events split across files all fold to the same state, which
  is what makes a union merge safe. Event ids are content-derived, so a duplicate arriving
  from a merge cannot change the result. Sequence numbers from the single writer give total
  order.

  **A wall-clock timestamp is evidence and nothing branches on it.** That is the rule, not a
  preference: the clock defect this kit exists partly to escape cost two tracks of
  workaround, and the log's own ordering must not inherit it.

  An unknown event kind is **counted and reported, never folded and never an error**, so an
  older reader meeting a newer ledger degrades rather than refusing. A *known* kind carrying
  a payload it cannot mean is refused, because silently skipping it would fold a record to a
  state no event ever wrote (`basicly-vkh0.11`).

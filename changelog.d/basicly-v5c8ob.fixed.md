- **`docs-citations` no longer passes over a citation it cannot read.** Two defects in one
  gate, and the second is the one that matters. A citation whose path is backticked and whose
  line number is not — `` `loop.py`:120 `` — matched nothing at all, so it was not counted,
  not checked and not reported: the one outcome a presence-based gate cannot tell apart from
  a document that carries no citations. The cause is not the lookbehind the report named,
  which passes against an opening backtick, but the **closing** one, which sits between the
  path and the colon whenever an author ticks the path and leaves the number outside. The
  pattern now takes that tick as part of the boundary. Loosening it admits no prose: over all
  304 tracked `.md` and `.yaml` files the old pattern and the new one both find 53 citations,
  and a document writing a clock time, a ratio, a count after a backticked command and a bare
  continuation reference yields none of them.

  The second: the gate was **fail-open on every citation it could not verify**. Its symbol
  rule only runs when the citing sentence names a top-level symbol of the module it cites,
  and 32 of the 44 citations in `docs/` name none — so they were counted, reported as a
  coverage share, and passed. Probed on real input, a sentence citing a real module at line 1
  with a false claim about what is there was one of the 32, and the gate exited zero over it.
  Each such citation is now a finding of its own kind, ratcheted per document in
  `[tool.docs_citations.unverifiable]`: the 32 that existed are recorded debt that may only
  fall, and a document absent from the list may carry none. The repair a reader is sent to
  make is to name, in the citing sentence, the symbol the claim is actually about — which is
  what makes the claim checkable, and what the symbol rule then holds it to (basicly-v5c8ob).

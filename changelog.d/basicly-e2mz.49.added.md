- **`code-citations` is a `[[verify.checks]]` entry: a section mark written in code, pointing at a
  document, is now checked against the headings that document defines.** `docs-citations` only ever
  ran the other direction — a `file.py:line` written in a document — so a `§N` in a comment or a
  docstring was checked by nothing at all, and a mark that resolves to the *wrong* section is worse
  than none because it reads as correct (`basicly-e2mz.49`).

  Measured over tracked Python in `src/`, `tests/`, `.scripts/` and `.basicly/core/`: **370 marks in
  94 modules**, of which **220 reached no heading**. Two of them cite
  `gates-and-rework-design.md`, absorbed and deleted 2026-08-08; four in the shipped tracker kit read
  as the kit's own source document while meaning the architecture. Those are the citations the two
  document absorptions blocked on this check would otherwise have orphaned with every gate green.

  **A citable target is a document and a number, both nameable.** The document is a `.md` path on the
  citing line, or a path-prefix binding in `[tool.code_citations.bindings]`; the number must match a
  numbered heading — `## 4. Title`, `### 4.6 Title` — the document defines today, which is the surface
  the architecture's section 3 promises a citation may rely on. A mark missing either half is
  **unresolved** and is a finding, not a silent pass: `docs-citations` counts 32 citations it cannot
  verify and exits zero, and that is exactly the shape this gate refuses.

  A **binding** is one reviewable line that made the kit's 113 bare marks checkable, and it is
  ratcheted against `binding_count` in both directions — added quietly, one binding could make a whole
  directory's marks resolve against a document nobody chose. A binding whose prefix stopped matching
  anything is reported rather than silently satisfied.

  **A ratchet, not a ban.** The 220 already-unresolved marks are recorded per module in
  `[tool.code_citations.frozen]` and may only fall; a module absent from that closed list may carry
  none. **No `fix_command`, and the omission is the point**: a mark whose section was absorbed into
  another document has no derivable target, and repointing a number whose sentence also went stale
  repairs the pointer and leaves the false claim.

  Like `docs-citations` and `module-size`, this is basicly's own gate rather than something `basicly
  install` projects: a consumer's document set is its own decision with its own frozen list and its
  own bindings.

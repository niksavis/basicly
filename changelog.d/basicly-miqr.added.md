- **`docs-citations` is a `[[verify.checks]]` entry: a `file.py:line` written in a document is now
  checked against the code it points at.** Nothing read one before. `docs-claims` gates generated
  blocks and `corpus-drift` gates an epic's problem statement, so a claim recorded in a requirements
  document on one day and refuted by the next day's commit kept asserting itself — measured on this
  repo's own plan, where four such claims sent a session at a P0 against a remedy the tree had
  already replaced (`basicly-miqr`).

  `.scripts/check_docs_citations.py` applies two exact rules and refuses to guess past them. A cited
  line must be **live code** — past end-of-file or blank is a citation that has certainly drifted. And
  when the citing sentence also names a **module-level** `def`, `class` or assignment of the cited
  file, in backticks or bare inside a fenced block, the cited line must fall inside that symbol; the
  failure prints the line the symbol moved to. A citation whose sentence names no symbol of the cited
  file is reported as *uncheckable* rather than as a pass, so the summary's coverage share can never
  be mistaken for the population. Module level only, and the module's own stem excluded, because a
  local named `total` or a word matching the filename matches half this repo's prose and would turn
  an exact rule into a coin toss.

  **A ratchet, not a hard gate.** Four citations were already stale in documents no single lane
  should rewrite, so the go-live debt is recorded per document in `[tool.docs_citations.frozen]` and
  may only fall — a document absent from that list may not carry one stale citation. **No
  `fix_command`, and the omission is the point**: renumbering a pointer whose surrounding sentence
  has also gone stale repairs the citation and leaves the false claim.

  Like `module-size` and `tree-growth`, this is basicly's own gate rather than something `basicly
  install` projects: a consumer's document set is its own decision with its own frozen list.

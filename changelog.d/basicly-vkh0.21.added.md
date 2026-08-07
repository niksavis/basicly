- **A new `kit-deployment` gate enforces the two host rules the tracker kit needs, instead
  of stating them in a docstring.** Both were required in prose and satisfied nowhere:

  - the event log must be declared `-text`, or a normalising checkout rewrites bytes whose
    event ids are **content-derived** — so a rewritten byte is a changed id, and every later
    id with it;
  - the ledger's derived files must be ignored, or a fold of the log gets committed beside
    it and recreates the dual-store failure the event log exists to escape.

  The gate reads the log glob and the derived-file patterns off the host's own kit rather
  than spelling them a second time, asks **git** what it does with sample paths
  (`check-attr`, `check-ignore`, `ls-files`) rather than reading the config text, and fails
  naming the rule the host lacks and the exact line to add. `--repo` points it at any
  checkout, so a consumer can check its own.

  This repo's ignore rules name the two derived files individually rather than ignoring the
  ledger directory: `.basicly/ledger/` is a *committed* directory, and a rule that swallowed
  the log would delete the truth to save a cache (`basicly-vkh0.21`).

- **A credential-shaped value is masked before it can reach the committed event log.** On
  2026-08-16 a comment body was passed to a shell inside double quotes with backticks in it; the
  shell ran them as command substitution and one expanded the whole environment into the write —
  152 assignments, 40,325 characters, including a live 32-character session token. It went
  through the write seam, so it reached the store, and running the existing redaction afterwards
  changed nothing relevant. The ledger is append-only and ships in every clone, so this is the
  one surface with no undo; nothing had been committed, which is the only reason the repair was
  an edit to two working files rather than a history rewrite.

  The three existing rule sets were blind to it by construction. The value was 32 characters of
  hex with no prefix, so nothing about its *shape* identified it, it was not a path, and the
  variable names were not the running user's. What identifies it is the name beside the equals
  sign: an uppercase identifier ending in `TOKEN`, `SECRET`, `KEY`, `PASSWORD` or `PASSWD`
  followed by a value, and separately a run of `NAME=value` lines, which is a dump whether or
  not any single line looks like a credential. Both are now redacted to a labelled placeholder
  at the write, and `redact_committed` is composed as environment, then secrets, then paths, then
  identity.

  **The larger hole was that second stage.** `redact_secrets` — the high-signal credential
  shapes — had only ever run on surfaced runner output and never on the committed path, so a
  hand-written credential in a recognised format went into permanent history verbatim. It runs
  there now, and across this repository's docs, source, tests and catalog it matches zero lines,
  so closing it cost nothing. Driven end to end through the real write seam into a throwaway
  ledger with the previous redaction as the control, and the stored ledger was probed
  separately, because a write-time guard says nothing about what is already on disk
  (basicly-vkh0.33).

- **`basicly tracker write` can now say a re-record is deliberate.** A field driven to A, to B
  and back to A folded to B: the third write digested to the first write's event id and was
  skipped. `--again` records it a second time - not idempotent, so every run appends. Four
  verbs that silently dropped an unreadable flag now refuse it (basicly-z9bggw).

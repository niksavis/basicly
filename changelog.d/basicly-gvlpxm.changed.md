- **A `change-summary` artifact carries a changed-path count and digest instead of the
  path list.** The list was the only field that grew with the diff — 4096 of the largest
  recorded summary's 18555 bytes were sorted paths — and it is the only field a reader can
  recover from the `commit` the same payload carries: `git show --name-only <commit>` for a
  build that committed once, `git log --name-only <base>..<commit>` otherwise, checked
  against `changed_digest`. A 400-file landing now stores a body under 1 KB. Summaries
  recorded before this are still accepted, so nothing already handed on is refused
  (`basicly-gvlpxm`).

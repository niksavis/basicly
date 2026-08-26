- **A lane whose work is already committed now lands instead of being dispatched again.** A
  supervisor pass re-derives the landing-only set from git — commits ahead of base, a clean
  tree, no repair brief — so work that outlived a crashed supervisor reaches the merge queue
  rather than paying for a second implement run.

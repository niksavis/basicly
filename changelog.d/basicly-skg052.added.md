- **A rendered surface is not exercised until its rendering has been looked at.** The new
  path-scoped `rendered-surfaces` rule says so on the board modules, the templates and the
  site, and `.scripts/check_render_overflow.py` measures it: every element whose scroll size
  exceeds its client size *and* whose box hides the difference. A declared ellipsis and a
  scrollable box are not clips, and the script measures the viewport asked for rather than a
  window of that size. It fails rather than skips with no browser, and is deliberately not a
  verify check because continuous integration has none (`basicly-skg052`).
- **`release-notes` names the fragment the base branch already holds.** A lane branched
  before a record closed on base was refused every commit over a note that existed one tree
  away, and answered by declaring the record invisible with a control that was true at its
  branch point and false on arrival. The refusal now says which file and says rebase
  (`basicly-skg052`).

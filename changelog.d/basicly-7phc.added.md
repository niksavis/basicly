- **A closed record that produced no release note now refuses the cut.** `changelog.d`
  could only ever check a fragment that *exists*, so nothing bound on a record that
  produced none — and 19 of the 54 records closed for v0.9.0 shipped with no note,
  including the seven specialist agents and five loop skills `basicly install` vendors to
  every consumer. The release workflow reads `CHANGELOG.md` from the **tagged** commit, so
  a note written afterwards can never reach the published release. The new `release-notes`
  check ratchets it: a closed record whose declared `## Scope` reaches a shipped surface
  (`src/basicly/`, `.basicly/core/`, `README.md`, `site/`) with no fragment named for it
  and no parenthetical citation in a fragment body or in `CHANGELOG.md` is named and
  refused, at the commit that closes the record and again in `basicly release`'s own
  refusals. It judges only a record that declares a machine-readable scope, so a record
  that closed before that convention is not reported; the 145 already unaccounted for are
  frozen in `[tool.release_notes.frozen]` so the backlog does not block a cut while a new
  omission does; and a change genuinely invisible to a consumer is declared in
  `[tool.release_notes.invisible]` with its reason, validated against the population it
  exempts from (basicly-7phc).

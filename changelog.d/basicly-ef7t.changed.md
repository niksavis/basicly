- **A lane declares a verify check and a ratchet delta in its own file.** `basicly.d/<bead-id>.toml`
  now carries a lane's `[[verify.checks]]` entries and its `[ratchet.<gate>]` contributions, and
  `basicly verify`, the pre-commit hook runner and the two ratchet gates all assemble the
  fragments on top of `basicly.toml` and `pyproject.toml`. Every ratchet number in a fragment is a
  delta rather than a total, so lanes compose by addition in any landing order. Appending to those
  two shared anchors bounced three of five lanes on the 2026-08-08 pass; the collision is now
  impossible by construction rather than detected, as `changelog.d` already made it for
  `CHANGELOG.md` (`basicly-ef7t`).

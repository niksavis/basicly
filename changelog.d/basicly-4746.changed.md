- **A lane records its changelog entry in its own file, so two lanes can never
  collide on one anchor.** A lane writes `changelog.d/<bead-id>.<category>.md`
  instead of editing `CHANGELOG.md`. The filename carries the bead id, so it is
  unique by construction and the collision becomes *impossible* rather than
  detected — the shape that blocked three of the four unattended-run attempts on
  2026-08-05/06 was two lanes at one anchor in a file no bead declared, each attempt
  in a different file, so enumerating them could never finish.

  `basicly release` assembles the fragments into the dated section, grouped under
  their Keep a Changelog heading and ordered by category then filename, and deletes
  them in the release commit. A hand-curated `## [Unreleased]` body still publishes
  alongside them, and a fragment whose category the operator already opened is
  appended to that section rather than opening a duplicate heading. An empty
  fragment, an unparseable filename, or a changelog with no `[Unreleased]` heading
  refuses the release before anything is written, because a lane's release note is
  never allowed to vanish quietly.

  `CHANGELOG.md` therefore leaves `[worktree] append_only_paths`, which is the point
  rather than a regression: that list bought detection by serializing every lane
  that touched the path, and there is now nothing to serialize (`basicly-4746`).

- **A generated path now declares its own rebuild command, and a partly generated file
  is safe to declare.** `[worktree] generated_paths` and `[worktree] regenerate_command`
  are replaced by one keyed table, `[worktree.regenerate_commands]`, mapping each
  generated path to the argv that rebuilds it. The old keys are refused by name, because
  one repo-wide command was silently a no-op for any artifact it could not write — this
  repo declared `basicly build` and the implementation plan's `docs-claims` block was
  rebuilt by nothing. A landing rebase now runs each conflicted path's own command, and
  bounces to the lane rather than staging a path whose rebuild left a conflict marker
  behind, so a file that is generated in one marked block and hand-authored around it can
  be declared without risking the hand-written half (`basicly-3w51`).

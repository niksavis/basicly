- **A lane is no longer refused at landing for a generated file it is fenced out of repairing.**
  A lane whose diff only *adds* a file leaves a declared-regenerable block counting a tree that
  no longer exists, and the documents those blocks live in are outside every lane's scope by
  design — so the landing verify failed the lane for a defect it may not touch, and the lane
  spent its whole rework budget discovering that. Two lanes escalated on it in one day while a
  third, which modified files and added none, landed cleanly on the same base at the same
  moment.

  `rebase.refresh_generated` now runs every command declared in
  `[worktree.regenerate_commands]` against the rebased worktree before the landing verify and
  commits what changed. Committed rather than left in the tree, because the landing merges the
  branch and an uncommitted rebuild would pass verify and never reach the base. The existing
  rebuild fired only on a merge *conflict* confined to those paths; staleness needs no
  conflict, because the rebase changed the tree the artifact is derived from, and the two are
  the same class.

  When regeneration does not make the file current, the landing **names the path and the
  command that rebuilds it** instead of reporting a plain verify failure. It reads that out of
  the failing check's own captured output rather than rebuilding a second time to find out,
  because probing by rebuild would dirty the lane's worktree on the way to refusing it, and it
  reads the same declared map that `loop preflight` prints, not a second list beside it
  (basicly-e2mz.35).

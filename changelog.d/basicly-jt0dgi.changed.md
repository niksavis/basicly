- **`basicly skills-build` and `skills-check` now cover every default skills root with no
  flag.** A bare run writes and checks both `.claude/skills` and `.agents/skills`, and the
  check names the roots it inspected. `--root` still narrows to one; `--all-default-roots` is
  accepted as a no-op with a deprecation note (basicly-jt0dgi).

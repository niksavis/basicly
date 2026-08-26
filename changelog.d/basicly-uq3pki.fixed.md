- **A gate run in a worktree no longer prints a `VIRTUAL_ENV` warning `uv` was already ignoring.**
  The engine is launched with `uv run` from the base checkout, so every verify check and lane
  dispatch it started in a worktree inherited that checkout's `VIRTUAL_ENV` and warned once per
  `uv run` check. It is now dropped when the cwd is a different checkout, and kept when it is not.

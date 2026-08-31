- **A session-start hook puts the ledger's orientation in the agent's context.** A new
  `sessionstart` stage projects to Claude Code's `SessionStart` and Copilot's `sessionStart`,
  running `basicly session start` before the first turn - plain text for Claude, an
  `additionalContext` object for Copilot. Bounded at 10s, silent with no tracker, never a
  gate (basicly-yru8eu).

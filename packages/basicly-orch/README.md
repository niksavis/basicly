# basicly-orch

`orch-state-hook` reads one vendor hook event on standard input and sets or clears a
display-only state label on the herdr pane it runs in. It never decides a permission and
writes nothing to standard output.

| Hook event | Label |
| --- | --- |
| SessionStart, UserPromptSubmit, PostToolUse | cleared |
| PermissionRequest | `blocked`: `needs you: <tool> <first word of the command>`, 10 minutes |
| Stop with background work | `idle` and `done`: `paused: <n> background`, 30 minutes |
| Stop without background work | cleared |

Outside herdr (`HERDR_ENV` unset) it does nothing. Each call to herdr has a 1-second
timeout, and any error ends the hook with exit code 0.

The design is `docs/design/session-state.md`.

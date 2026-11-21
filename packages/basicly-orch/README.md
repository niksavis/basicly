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

## orch relay commands

| Command | What it does |
| --- | --- |
| `orch record` | prints the one active record whose title starts with `orchestration-record:`; refuses 0 or several and names the count |
| `orch decide` | an ask-first item only: refuses a missing rule, rule date, reason, condition, proof command or response if the proof fails, writes the decision on the record and prints the message |
| `orch approve` | an item the person approves through the orchestrator only: refuses ask-first items and items that stay in the worker's window by name, writes the relayed approval on the record and prints the message |
| `orch send --order` | refuses an order without goal, limits and expected proof, or with a placeholder, and prints the order |
| `orch report` | accepts only `done`, `blocked` and `needs-decision` with a record id, writes the report on that record and prints it |
| `orch start` | prints the launch command for `claude`, `copilot` or `codex`; Codex gets the projected role file as developer instructions |
| `orch brief` | prints the orchestration record id, the newest `[handover` note, the `[owner` notes after it, the session map and the later notes, in at most 20,000 bytes; it says how many older notes it left out |
| `orch accept` | a worker's check of a relayed decision or approval: `--record`, `--repo` and `--action` must match the relay exactly, and the relay must be within 60 minutes of its entry stamp (an until-landed diff approval has no time limit); it refuses any difference by name |

`orch status` shows each session as `working`, `quiet`, `nudge` (no update for 30 minutes), `stale` (no update for 2 hours), `needs you` or `dead`, and folds the dead sessions into one count line.

`--risk '<risk> => <check>'` on `orch decide` and `orch approve` must name a check that is a line of `--action`. The item classes are one list in `orch_relay/classes.py`. The commands write records through the repository's own tracker kit, and run the secret scanner on the text first. They print the message; the vendor's own messaging carries it until the mail store moves into the kit.

# basicly-orch

Install or upgrade the `orch` command from a basicly release; the package version is the basicly version:

```sh
uv tool install --force --python '>=3.14' 'git+https://github.com/niksavis/basicly@v0.49.4#subdirectory=packages/basicly-orch'
```

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

## orch pace

| Command | What it does |
| --- | --- |
| `orch-statusline-tap -- COMMAND [ARG...]` | the status line command wrapper: passes the status line input to COMMAND, prints its output byte for byte with its exit code, and saves `rate_limits` to its usage snapshot file `statusline.json`. A snapshot error never stops the output |
| `orch pace` | prints used percent, reset time, burn rate and projection at reset for the 5-hour and 7-day windows, and the advice `start`, `hold` or `close idle`; refuses a missing snapshot or one older than 10 minutes |
| `orch pace note RECORD start\|done` | writes the 5-hour percent on the record; `done` also writes the change since `start` and the record type |

The snapshot folder, fields, write rule and reading rule are in `docs/design/usage-snapshot.md`.

## orch relay commands

| Command | What it does |
| --- | --- |
| `orch record` | prints the one active record whose title starts with `orchestration-record:`; refuses 0 or several and names the count |
| `orch decide` | an ask-first item only: refuses a missing rule, rule date, reason, condition, proof command or response if the proof fails, writes the decision on the record and prints the message |
| `orch approve` | an item the person approves through the orchestrator only: refuses ask-first items and items that stay in the worker's window by name, writes the relayed approval on the record and prints the message |
| `orch send --order` | refuses an order without goal, limits and expected proof, or with a placeholder, and prints the order |
| `orch report` | accepts only `working`, `done`, `blocked` and `needs-decision` with a record id, writes the report on that record and prints it; it sends no message, so its last line names the send step |
| `orch start` | prints the launch command for `claude`, `copilot` or `codex`; Codex gets the projected role file as developer instructions |
| `orch brief` | prints the orchestration record id, the newest `[handover` note, the `[owner` notes after it, the session map and the later notes, in at most 20,000 bytes; it says how many older notes it left out |
| `orch install --release X.Y.Z --repo NAME=RECORD [--dry-run]` | installs a basicly release in a lane `kit-XYZ` of each named workspace repository: `basicly worktree create`, `basicly install` and a commit `chore(basicly): install the released kit (RECORD)` through that repository's own hooks, all from the release wheel; prints the exact commit per repository. The record must be claimed in that repository's tracker, or its commit-msg hook refuses. It refuses a dirty lane, a bad name or version, and a repository the workspace does not hold, before it writes. `--dry-run` names the lane, the wheel, the subject and the install dry run, and writes nothing. A second run on a lane whose head is already that commit prints it and commits nothing. It never pushes; each project session lands its own lane |
| `orch delegate --vendor claude\|codex\|copilot --record RECORD --prompt TEXT [--model M]` | hands one turn to another coding agent: it runs the agent headless in the lane `delegate-RECORD-VENDOR` of the current repository (made with `basicly worktree create`), writes the answer as a comment on RECORD, and prints the agent's session id and resume command. `orch delegate --resume SESSION --prompt TEXT` continues that session in the same lane. A usage-limit answer exits 3 with `usage limit:` and the reset time when the agent gives one. It refuses an unknown coding agent, a bad record id and an unknown session by name before it starts anything |
| `orch accept` | a worker's check of a relayed decision or approval: `--record`, `--repo` and `--action` must match the relay exactly, and the relay must be within 60 minutes of its entry stamp (an until-landed diff approval has no time limit); it refuses any difference by name |

`orch lesson add` and `orch lessons end-session` stamp the member name from `ORCH_MEMBER`, or else from the session's orch state record (the name `orch state set` shows).

`orch status` shows each session as `working`, `quiet`, `nudge` (no update for 30 minutes), `stale` (no update for 2 hours), `needs you` or `dead`, and folds the dead sessions into one count line.
`orch state set --item ID --last TEXT [--failed]` records the session's current record and its last result (`item`, `last`, `last_at`, `last_ok` in the session file, at most 200 characters each), and `orch status` shows them in the row. The post-commit and pre-push hooks, the tracker claim, the landing tool and its CI wait write `item` and `last` themselves when an orchestrator store and a session id exist, and do nothing otherwise. `runner_timeout` is off by default. A dispatch that runs 30 minutes with no end, or past a `runner_timeout` you set, writes `last` as stalled and keeps running; no dispatch is killed at a time limit.

`--risk '<risk> => <check>'` on `orch decide` and `orch approve` must name a check that is a line of `--action`. The item classes are one list in `orch_relay/classes.py`. The commands write records through the repository's own tracker kit, and run the secret scanner on the text first. They print the message; the vendor's own messaging carries it until the mail store moves into the kit.

## Coding agents

`orch delegate` drives each coding agent through an adapter with one interface: the command line for a new turn and for a resumed turn, the reply (the final answer and the session id), and the agent's own resume command.
The built-in adapters are `claude` (Claude Code `-p --output-format json`, edits accepted), `codex` (`codex exec --json`, workspace-write sandbox) and `copilot` (`copilot -p --silent`), in that order.
A package adds a coding agent by naming an adapter object or class in the entry point group `basicly_orch.adapters`; a built-in name cannot be replaced.
The lane and the agent's own sandbox are the only isolation in this version.
The cheapest models measured on 2026-11-26 are `haiku` for Claude Code, `gpt-6-luna` for Codex and `mai-code-2-flash` for Copilot; pass one with `--model`.

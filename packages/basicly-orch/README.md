# basicly-orch

Install or upgrade the `orch` command from a basicly release; the package version is the basicly version:

```sh
uv tool install --force --python '>=3.14' 'git+https://github.com/niksavis/basicly@v0.51.0#subdirectory=packages/basicly-orch'
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
| `orch pace` | prints used percent, reset time, burn rate and projection at reset for the 5-hour and 7-day windows, and the advice `start`, `hold` or `close idle`, the `fit:` count of working sessions that fit until the 5-hour reset, a `sessions:` line that names how many sessions to start, to hold or to let finish (needed rate `(100 - used) / hours to reset` against the burn per working session; with no working session or no measured burn it assumes `session_rate_pct_per_hour` from `pace.json` in the orch store, default 5%/h, and says so), then a table of cost per record type from the `[pace done]` notes; refuses a missing snapshot or one older than 10 minutes |
| `orch pace note RECORD start\|done` | writes the 5-hour percent on the record; `done` also writes the change since `start` and the record type. A landed vault claim and a merged landing write the same notes by themselves |

The snapshot folder, fields, write rule and reading rule are in `docs/design/usage-snapshot.md`.

## orch automode

Auto mode reads its rules only from the user settings `~/.claude/settings.json`, managed settings or `--settings`, never from a project. `orch automode` writes the rules that an orchestrated workspace needs into the user settings: the lane git work, the guarded landing, the guarded lane cleanup, release tags, the delete rule and the environment entries for the repositories.

| Command | What it does |
| --- | --- |
| `orch automode guide` | prints the steps: run `/auto-mode-setup`, then `orch automode plan`, then `orch automode apply`, then `claude auto-mode critique` |
| `orch automode plan --config FILE [--settings PATH]` | prints the change to the settings file as a diff, each conflict with a permission rule, the size of the rules in characters, and a plan token. It writes nothing. It exits 1 when a conflict is left |
| `orch automode apply --config FILE --plan TOKEN [--settings PATH]` | refuses when the settings file or the rules changed since the plan, or when a conflict is left. It first checks the planned file in a temporary Claude Code profile, then writes a backup `settings.json.orch-automode-<UTC time>` and the new file, and runs `claude auto-mode config` again. When Claude Code does not show every planned entry, it restores the file and refuses |

The config file names the repositories:

```json
{"host": "github.com", "owner": "<account>", "workspace": "~/development",
 "repositories": [{"name": "<repo>", "visibility": "public", "project": true, "tag": "v<version>"},
                  {"name": "<private-repo>", "visibility": "private", "project": false}],
 "omit": [], "drop": []}
```

- A `project` repository is a folder `<workspace>/<name>` with lanes. `tag` is `v<version>` or `<name>--v<version>`. `omit` names generated rules to leave out. `drop` names labels of the user's own entries to remove, for example a rule that a deny rule makes dead.
- Each value must match its token class. Any other character is refused by name before any text is made.
- The rules are one source, `orch_automode/rules.json`. The token and guard definitions are written once, in the entry "Shared Terms". The critique of the rules returns nothing once the rules are long, so they stay short.
- The plan replaces an entry whose label (the text before the first colon) is a generated label, and keeps every other entry. Each list keeps `$defaults` first.
- A deny rule blocks a command before the classifier sees it, and an ask rule forces a prompt. The plan reads the deny and ask rules of the settings file and of each project's `.claude/settings.json` and `.claude/settings.local.json`. It names each generated command that one of them matches. Remove the pattern or omit the rule.
- `claude auto-mode config` exits 0 and prints the defaults when the settings file does not parse. So apply checks that each planned entry appears in its output, not the exit code.
- A guard fails closed. The tests run each guard in a temporary repository: the cleanup guard refuses a lane branch, local or remote, with a commit outside `origin/main`, a dirty lane folder and a lane folder that git cannot read. The tag guard refuses a commit outside `origin/main`. The landing guard refuses a lane that changes a guarded path.

## orch relay commands

| Command | What it does |
| --- | --- |
| `orch record` | prints the one active record whose title starts with `orchestration-record:`; refuses 0 or several and names the count |
| `orch decide` | an ask-first item only: refuses a missing rule, rule date, reason, condition, proof command or response if the proof fails, writes the decision on the record and prints the message; `--record` names a record of the workspace ledger (default: the orchestration record), `--project-record` names the housed project record that the decision is about, and `orch accept` takes either |
| `orch approve` | an item the person approves through the orchestrator only: refuses ask-first items and items that stay in the worker's window by name, writes the relayed approval on the record and prints the message |
| `orch send --order` | refuses an order without goal, limits and expected proof, or with a placeholder; prints the order under a first line "printed, not sent", and delivers nothing |
| `orch report` | accepts only `working`, `done`, `blocked` and `needs-decision` with a record id, writes the report on that record and prints it; it sends no message, so its last line names the send step |
| `orch start` | needs `--role`; `--role worker` starts the session through the session adapter `.orchestrator/bin/session` and refuses when there is none; any other role prints the launch command for `claude`, `copilot` or `codex` under a first line "printed, not started"; refuses `--role orchestrator` while an Orchestrator session is live; Codex gets the projected role file as developer instructions |
| `orch brief` | prints the orchestration record id, the newest `[handover` note, the `[owner` notes after it, the session map and the later notes, in at most 20,000 bytes; it says how many older notes it left out |
| `orch install --release X.Y.Z --repo NAME=RECORD [--dry-run]` | installs a basicly release in a lane `kit-XYZ` of each named workspace repository: `basicly worktree create`, `basicly install` and a commit `chore(basicly): install the released kit (RECORD)` through that repository's own hooks, all from the release wheel; prints the exact commit per repository. The record must be claimed in that repository's tracker, or its commit-msg hook refuses. It refuses a dirty lane, a bad name or version, and a repository the workspace does not hold, before it writes. `--dry-run` names the lane, the wheel, the subject and the install dry run, and writes nothing. A second run on a lane whose head is already that commit prints it and commits nothing. It never pushes; each project session lands its own lane |
| `orch delegate --vendor claude\|codex\|copilot --record RECORD --prompt TEXT [--read-only] [--model M \| --tier T] [--billing subscription\|console]` | hands one turn to another coding agent: it runs the agent headless in the lane `delegate-RECORD-VENDOR` of the current repository (made with `basicly worktree create`), writes the answer as a comment on RECORD, and prints the agent's session id and resume command. `orch delegate --resume SESSION --prompt TEXT` continues that session in the same lane. `--read-only` makes no lane: the agent runs read-only in the repository root (Claude Code `--permission-mode plan`, Codex `-s read-only`, Copilot `--deny-tool=write` with no allow rules), and a resumed read-only session stays read-only; Codex cannot resume one, so it refuses that by name. `--tier cheap` passes the model that the tier map `tiers.json` in the orch store names for that agent. A usage-limit answer exits 3 with `usage limit:` and the reset time when the agent gives one. Each non-zero exit of a coding agent is saved as a JSON file under `delegations/failures/` in the orch store (agent, exit code, the command with the prompt shown as `PROMPT`, stdout, stderr and time), and the refusal names that file. `orch delegate --list` prints each adapter with its origin and capabilities. It refuses an unknown coding agent, a refused plugin, a capability the adapter does not declare, a bad record id, an unknown session, an unknown tier, a tier with no model for the agent and `--tier` with `--model` by name before it starts anything |
| `orch accept` | a worker's check of a relayed decision or approval: `--record`, `--repo` and `--action` must match the relay exactly, and the relay must be within 60 minutes of its entry stamp (an until-landed diff approval has no time limit); it refuses any difference by name |

`orch lesson add` and `orch lessons end-session` stamp the member name from `ORCH_MEMBER`, or else from the session's orch state record (the name `orch state set` shows).

`orch status` shows each session as `working`, `quiet`, `nudge` (no update for 30 minutes), `stale` (no update for 2 hours), `needs you` or `dead`, and folds the dead sessions into one count line.
`orch state set --item ID --last TEXT [--failed]` records the session's current record and its last result (`item`, `last`, `last_at`, `last_ok` in the session file, at most 200 characters each), and `orch status` shows them in the row. The post-commit and pre-push hooks, the tracker claim, the landing tool and its CI wait write `item` and `last` themselves when an orchestrator store and a session id exist, and do nothing otherwise. `runner_timeout` is off by default. A dispatch that runs 30 minutes with no end, or past a `runner_timeout` you set, writes `last` as stalled and keeps running; no dispatch is killed at a time limit.

`--risk 'RISK => CHECK'` on `orch decide` and `orch approve` must name a check that is also a line of `--action`; one refusal names every risk that fails and prints an example that passes. The item classes are one list in `orch_relay/classes.py`. The commands write records through the repository's own tracker kit, and run the secret scanner on the text first. They print the message; the vendor's own messaging carries it until the mail store moves into the kit.

The session adapter is the workspace's own executable at `.orchestrator/bin/session`, beside the orch store; the kit does not ship one. `orch start --role worker` runs it as `session start NAME --dir DIR [--codex] [--prompt FILE]` and passes its output and exit code through.

## Coding agents

`orch delegate` drives each coding agent through an adapter with one interface: the command line for a new turn and for a resumed turn, the reply (the final answer and the session id), and the agent's own resume command.
The built-in adapters are `claude` (Claude Code `-p --output-format json`, edits accepted), `codex` (`codex exec --json`, workspace-write sandbox) and `copilot` (`copilot -p --silent`), in that order.
In a lane, Copilot gets `--allow-tool=write(LANE/**)` for the absolute lane path, a shell allow list (`git status`, `git diff`, `git add`, `git commit`, `uv run`, `pytest`), `--deny-tool` for `shell(git push)`, `shell(git reset)`, `shell(git checkout)`, `shell(rm)` and `url`, and `--disallow-temp-dir`; a deny rule wins over every allow, and in a `-p` run any other request is denied. The lists are data in `orch_relay/adapters.py`, and `orch delegate` prints them on its `rules:` line.
`orch delegate` refuses, before the turn starts, a command line that holds `--allow-all`, `--allow-all-tools`, `--allow-all-paths`, `--allow-all-urls`, `--yolo`, `--autopilot` or a bypass flag, so a plugin adapter cannot add one either.
A package adds a coding agent by naming an adapter object or class in the entry point group `basicly_orch.adapters`; a built-in name cannot be replaced.
Each adapter declares its capabilities as one `Capabilities` value: `names_session` (orch delegate gives the session id), `resumes_read_only`, `read_only` and `lane_edits`. orch delegate reads only that value. A turn that needs a capability the adapter does not declare is refused before any process starts, and the refusal names the agent and the capability.
A plugin that fails to load, or lacks `argv`, `reply`, `resume_command` or `capabilities`, is refused by its entry point name only when it is used; the other agents still run.
`orch delegate --list` prints each adapter, its origin (`built-in` or `plugin` with its distribution) and its capabilities, or the reason a plugin is refused. It takes no other option and needs no orch store.
The lane and the agent's own sandbox are the only isolation in this version.
A saved failure can hold file paths, a user name and tokens, so it stays in the local orch store and is never committed as it is. A replay fixture under `tests/fixtures/delegate_failures/` is first passed through `orch_relay.failures.redact`, which uses the tracker kit's own redaction rules and replaces the user name; a test fails on a fixture that redaction would still change. The one fixture marked `source: fake` comes from the fake agent; the first real Codex usage-limit capture joins it to fit the usage-limit patterns.
The cheapest models measured on 2026-11-26 are `haiku` for Claude Code, `gpt-6-luna` for Codex and `mai-code-2-flash` for Copilot.
They make the tier `cheap` of the tier map, which `orch delegate --tier` writes to `tiers.json` in the orch store on first use; edit that file to change a model or add a tier, so the orchestrator names a tier and never a model id.
Read-only research uses `orch delegate --read-only --tier cheap`.

## Console billing

`orch delegate --billing console` runs Claude Code on an Anthropic Console budget instead of the subscription; `--billing subscription` is the default.
It reads `console.json` in the orch store, which the kit never writes: `settings` (a Claude Code settings file whose `apiKeyHelper` prints a Console token), `profile` (a Claude Code config folder that logs in the same way), `run_cap_usd`, `month_budget_usd` and `tiers`, each tier with a `model`, an `effort` and a `mode`.
The mode `bare` runs `claude --bare -p --settings SETTINGS`, which skips hooks, so it is only for `--read-only` work.
The mode `profile` runs `claude -p` with `CLAUDE_CONFIG_DIR=PROFILE`, so the project's hooks and gates load; it is for lane turns and refuses a working folder that is not a linked git worktree.
Each run gets `--max-budget-usd` with the run cap and `--effort` from the tier.
Only a model that a tier names is allowed: `--tier` picks a tier, and `--model` must match the model of exactly one tier.
Before a run, `orch delegate` takes an exclusive lock on `spend.lock` in the orch store, refuses with `console budget:` when the month's spend plus the run cap passes the month budget, and adds a `pending` entry with the run cap to the spend ledger `spend.jsonl`; so two delegates that start together cannot both pass the budget.
After the run, it adds a `settled` entry with the same id and `cost_usd` from `total_cost_usd` of the JSON result, and prints the spend of the run and of the month on its last line. Each entry holds the time, the UTC month, the record, the session, the tier, the model, the mode, `cost_usd` and `estimated`; the last entry of an id counts.
A run that prints no cost, such as a crash or a timeout, and a pending entry that is never settled, are charged the run cap and marked `estimated`.
It refuses by name a missing `console.json`, a missing or wrong setting, a model or tier that the config does not name, a coding agent other than `claude`, `--billing` on `--resume` and a ledger line it cannot read; a resumed session keeps its billing and tier.
The token never reaches `orch delegate`: it passes only the path of the settings file or the profile folder.

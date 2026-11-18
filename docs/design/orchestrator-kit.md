# Orchestrator kit

Status: design draft 4, 2026-11-17. Tracker: the "Orchestrator kit" tasks under `basicly-qpr2xmr.3`.
Drafts 1 to 3 were reviewed privately by two multi-lens reviews and one council. This draft is their public form, with the implementer's review applied.

## 1. Goal

One orchestrator session directs worker sessions that run on Claude Code, Codex CLI and GitHub Copilot CLI.
Each vendor's own messaging reaches only sessions of the same vendor, so the kit brings its own protocol.
A person talks to the orchestrator. An approval the person gives there can count in a worker for the items the workspace's own approval rule names.
The kit ships in basicly like the tracker kit. A consumer with no engine installs the `basicly-orch` package and gets the `orch` command, the mail store and the vendor adapters from it.

## 2. Scope and threat model

- All sessions run under one OS user on one machine. Any agent with a shell can write any file that user can write. The kit therefore provides mistake-proofing and an audit trail, not security against an agent that edits files on purpose. It never claims more.
- Prompt injection is real: an agent steered by text it reads may act against its rules. The kit never turns text into a permission. No hook in this kit returns allow. Every vendor permission prompt and classifier refusal stays with the person in that session's window.
- Version 1 builds on documented, stable interfaces only: files, a CLI and each vendor's hooks. Vendor message servers may become adapters later. herdr is an optional adapter of the `basicly-orch` package for session state ([session state](session-state.md)); the kit itself never calls it.
- One machine. Records carry a `host` field, and all state changes go through one store module (section 9), so a remote store later replaces one module.
- Requirement R0 of `basicly-qpr2xmr` holds: no vendor credential is collected, stored or brokered.

## 3. What the kit is made of

| Part | Source |
| --- | --- |
| Mail, locks, wait, member records | the agent-mail skill's three scripts, moved into the kit with the changes listed below |
| Registry fields | added to the member record: vendor, adapter, session ids, host |
| Orchestrator role | one lock named `role-orchestrator` |
| Work reports | the tracker: claim, close and comment. Mail does not repeat them |
| New | approval records (section 5), vendor adapters (section 7), `orch doctor` (section 11) |

The move changes three behaviours. Each change has its own tests (task "store and lock format version 2"):

1. The mail root is found by reading the `.git` file or folder instead of running `git`. Cases: a main checkout, a linked worktree with an absolute or relative `gitdir`, a submodule, and Windows paths.
2. The kit spawns no process. The git ignore check of `init` moves to the package (section 10).
3. The lock owner file gains a format version, the process id and the process start time, so a stale lock and the role lock can be checked for liveness. The old scripts read a version 2 lock as held. The kit reads a version 1 lock as held with unknown liveness.

## 4. Approval tiers in version 1

| Tier | Example | Through the kit? |
| --- | --- | --- |
| A. Policy items | ask-first and owner-only items in a project's rules | Yes, as an approval record (section 5). Mistake-proofing and audit, not a security control |
| B. Harness prompts | a tool permission prompt in a worker | No. The person answers in that window |
| C. Classifier refusals | an automatic mode refuses a call | No. The person decides in that window |

## 5. Approval records (tier A)

### 5.1 Enablement

- Off by default. A workspace enables approvals by committed configuration that names its orchestrator member. Without it, `orch approve` and `orch use` refuse by name.
- The workspace's own approval rule is the source. The kit enforces it mechanically and may be stricter, never looser.
- Never covered, refused by `orch approve` by name: a harness permission prompt or classifier refusal; skipping, weakening or bypassing a test, gate or hook; an action that destroys work and cannot be undone (force-push, history rewrite, deleting unmerged work or work the worker did not create, making a repository public); any change to the approval rule.

### 5.2 Minting

- `orch approve` is the only command that writes an approval. It runs only in the session that holds the `role-orchestrator` lock, and it sits under an ask rule there, so the vendor's own prompt shows the person the whole command before it runs.
- The command carries the full action as arguments: `orch approve --request <id> --to <worker> --record <record> --repo <path> --action "<exact command, path list, or diff file and its sha256>"`.
- A diff or a long action is written to a file in the request folder, mode 0600, and the command names the file and its sha256. The worker reads that file.
- `orch approve` refuses by name when an argument differs from the request, when the request is not pending, when the caller does not hold the role lock, or when the action names a never-covered item.
- "Approved in advance" for a change that does not exist yet is intent. `orch approve` needs the finished change.
- Permission mode: the kit's pre-tool hook in the orchestrator session records the session's permission mode for each `orch approve` call. `orch approve` refuses when that record is missing, older than 60 seconds, or names a mode that skips ask rules. The list of such modes comes from vendor documentation and is tested against recorded hook input, never by running those modes. Whether the ask prompt always appears in the normal modes, also after an answer of "don't ask again", is pending probe P6. On a vendor where P6 fails, `orch approve` refuses to run.
- Every worker carries a deny rule for `orch approve`, generated from one source (section 7). `orch doctor` fails when an allow rule in the orchestrator's settings matches `orch approve`.

### 5.3 Binding

- An approval for a change binds to the sha256 of its diff and to the blob ids of the named paths at approval time. `orch use` refuses when a named path changed. Unrelated commits, such as tracker notes, do not void it.
- An approval for "push this commit" binds to that commit id.
- An approval for a command binds to the exact argument list and the working directory.

### 5.4 Lifecycle

- Request states: `pending`, `approved`, `expired`. Approval states: `open`, `used`, `expired`.
- `orch request --action "<text>" --repo <path> --record <id> --why "<text>"` writes `requests/pending/<id>.json`. Ids come from the tracker kit's id generator.
- `orch approve` does, in this fixed order: write `approvals/open/<id>.json`, append one audit line, move the request to `approved/`, then send the worker a notice. It is re-runnable: run again on the same request, it finds the partial state and finishes only the missing steps.
- `orch use --approval <id> --action "<what the worker will run>"` compares the worker's action with the approval, checks the caller's name, record, repository, bindings and expiry, then moves the approval to `used/` by one rename and appends an audit line. If the action then fails, the worker requests again.
- Expiry: one constant, `APPROVAL_TTL`, at most the workspace rule's limit. Every `orch` command expires overdue items lazily. Pending requests expire with the same constant.

### 5.5 What the worker does

- The worker guidance says: before an ask-first or owner-only action, run `orch use`. If it says no match, stop and ask the orchestrator.
- This is a guidance rule. A worker that skips it is not stopped by the kit. The audit shows every owner-only action that has no used approval, where the project's own gates record such actions.

### 5.6 Audit

- `audit.log`, append-only. One JSON line per event, each with a length prefix and a checksum, written with fsync. `orch doctor --repair` detects and quarantines a torn last line, then appends an audited repair line.
- The audit holds the approved action text, ids, names, times and outcomes. It never holds tool input or secrets.

## 6. Roles and identity

- Every session is a worker by default. `orch join --role orchestrator` takes the `role-orchestrator` lock when it is free.
- `--takeover` breaks the lock only under the ask prompt in the new orchestrator session, so the person confirms every takeover. The audit records it.
- Identity is the member name. A name holds a list of session ids. A new session id binds to an existing name only when the old session is not alive, checked by the vendor process id and its start time from the session-start hook. A fork of a live session joins a new name. Probe P7 (2026-11-18, `orchestrator-kit-probes/p7-identity.py`) measured the SessionStart hook input:
  - Claude Code 2.1.327: `session_id`, `source` (`startup`, `resume`, `fork`) and, when the session has a name, `session_title`. A resume keeps the session id; a fork gets a new id and keeps the parent's `session_title`. The kit therefore treats `source: fork` as a new member and never re-binds a name from it. The vendor process is the parent of the hook's `sh` wrapper.
  - GitHub Copilot CLI 1.0.112: `sessionId` and `source` (`new`, `resume`), no session name and no fork. A resume keeps the id. The vendor process is the hook's direct parent. A launcher may pass `--session-id` to choose the id.
  - Codex CLI 0.169.0: pending probe P7 in a folder whose hooks are trusted.
  - Copilot loads repository hooks in a resumed prompt-mode session only with `GITHUB_COPILOT_PROMPT_MODE_REPO_HOOKS=true`, because `--trust-folder` cannot be combined with `--resume`.
- `orch join` refuses by name when another live session holds the name, and names it.
- Session start and window names (lesson L9, herdr adapter only): the kit's session start command takes one name and uses it for the session name and, with the herdr adapter, the herdr tab label (`herdr tab create --label`, `herdr tab rename <tab> <label>`, herdr 0.9.5). A tab that holds several session panes is labelled with all their names joined by ` + `, and the command renames it when a pane is added or closed. `orch doctor` reports a tab whose label differs from the sessions in it.
- Names follow `[a-z][a-z0-9-]{1,30}`. Any other name is refused with a normalized suggestion.

## 7. Vendor adapters

One interface, one module per vendor. Core code calls only the interface.

| Method | Purpose |
| --- | --- |
| `identity(hook_input)` | session id, custom name, permission mode, vendor process id and start time |
| `hook_entries()` | the hook entries and deny rules this kit needs, as data |
| `context(text)` | the vendor's hook answer that adds a line of context and makes no permission decision |
| `wake(member)` | the wake path, or "none" with a reason |
| `skips_ask(mode)` | whether a permission mode skips ask rules |

- The package installer writes hook entries into settings (section 10), not the adapter.
- A client that also reads another vendor's hook settings gets `--adapter <name>` on each entry, and a hook does nothing when the input is not in its adapter's shape. `orch doctor` flags a hook that ran twice for one event.

## 8. Messages and delivery

- Kinds: `order`, `question`, `answer`, `approval-notice`. Reports go to the tracker.
- Envelope: `id`, `from` (the member name bound to the caller's session, set by the tool), `to`, `kind`, `record`, `reply_to`, `sent_at`, `host`.
- `orch send` refuses an unknown recipient, or one not seen within `MISSING_AFTER`, by name, unless `--queue` is given.
- Busy session: a hook adds one context line, "N unread from <name>: run orch read", at UserPromptSubmit, and at PostToolUse at most once per new message id. Probe P9 (2026-11-17, `orchestrator-kit-probes/p9-context.py`) measured that both reach the model in the same turn:
  - Claude Code 2.1.326: `hookSpecificOutput.additionalContext` with `hookEventName`, exit 0, at both events.
  - GitHub Copilot CLI 1.0.112: top-level `additionalContext`, exit 0, at `userPromptSubmitted` and `postToolUse`. The hooks reference says command-hook output at `userPromptSubmitted` is dropped; the probe shows that it reaches the model.
  - Codex CLI 0.169.0: the same nested format as Claude at both events, per its hooks documentation and source; pending probe P9 in a folder whose hooks are trusted, because Codex runs a project hook only after its hash is trusted.
  - A control run with silent hooks reported none of the tokens on either measured vendor.
- No context line at Stop. On all three vendors the only way a Stop hook reaches the model is to block the stop with a reason, which forces another model turn (Claude Code and Copilot cap this at 8 in a row; for Codex no cap was found). A notice there would cost a turn every time a turn ends.
- Idle session: the session keeps one blocking `orch wait --until-mail --max <seconds>` running as a background or long-running shell call. While it blocks, the model spends no tokens. Polling is done by the `orch wait` process (file stat every 2 seconds), never by the model. On timeout the wait restarts as one short tool call. The longest blocking call per vendor, whether a background wait keeps the session alive and wakes it, and the token cost of one empty cycle are pending probe P8.
- A message moves to `cur` only on `orch read`. `orch who` lists, per member: unread count, oldest unread age, last seen, a missing wait, and vendor version.
- An `order` unread for `ESCALATE_AFTER` is flagged by `orch who` as overdue.

## 9. Modules and dependencies

| Module | May import |
| --- | --- |
| `store.py`: atomic write, rename, append, lock | standard library |
| `mail.py` | store |
| `members.py`: names, session ids, liveness data | store |
| `adapters/*.py` | standard library only, no kit module |
| `approvals.py` | store, members, mail (only to send a notice) |
| `cli.py`, the `orch` command | all of the above |

- Mail never imports approvals. Approvals reach mail only through `send`.
- `--record` is opaque text to the kit. It never reads the tracker.
- Constants are named once: `APPROVAL_TTL`, `MISSING_AFTER`, `ESCALATE_AFTER`.

## 10. Packaging and installation

- Kit: `.basicly/core/kit/orch/`. Package: `packages/basicly-orch`. Version pin like the tracker's. Python floor: the kits' floor, 3.14, which is higher than the agent-mail skill's 3.12. The migration keeps the skill available for consumers on older Python until they move.
- Git ignore checks, vendor version checks and the doctor canary live in the package, where the installer already runs subprocesses.
- In a repository that basicly manages, `hooks.yaml` projects the kit's hooks for all three clients, and `orch init` refuses by name.
- In a standalone repository, `orch init` writes the hook entries and deny rules from `adapter.hook_entries()`, records each written entry in a manifest after it succeeds, and shows the diff. Re-running `orch init` resumes from the manifest. `orch uninstall` removes exactly the manifest entries.
- Hook entries and deny rules change permission settings. A person sees the exact diff per project before it is written.
- A consumer workspace gets the kit's hooks only through a basicly release and `basicly install`. A workspace pinned to an older basicly gets the kit after its next upgrade.

## 11. Doctor and repair

- `orch doctor`: per member, hooks present and answering a canary, deny rule on `orch approve` present, no allow rule matching `orch approve` in the orchestrator, permission mode of the orchestrator, stale locks, double hook runs, vendor version against the tested list.
- The session-start hook records the vendor version, so `orch who` shows a member on an untested version.
- `orch doctor --repair` quarantines unreadable files and a torn audit line, and releases locks whose process is not alive, each with an audit line. It never deletes mail or approvals.
- A missing or empty root is reported. Mail in flight is lost in that case; version 1 states this and does not back mail up.

## 12. Migration from an existing mail tool

1. Announce the switch and stop all writers of the old tool.
2. Read every live mailbox. Archive retired mailboxes.
3. `orch doctor` checks the folder; then add the `agent-mail-root` marker.
4. Install the kit through `hooks.yaml`. Record the projects switched.
5. The old tool checks for the marker and refuses with the matching `orch` command.
6. Rollback until `orch doctor` has passed for a day: uninstall the kit hooks in each switched project, remove the marker, and the old tool works again. No mail moves.

Probe P10 established that no mail moves: a shell mail tool and the agent-mail Python scripts read each other's mailboxes and locks (`orchestrator-kit-probes/p10-mail-compat.sh`).

## 13. Probes before code

Each probe has its own task under `basicly-qpr2xmr.3` and runs in scratch, never in a live mail root or in user settings.

| Id | Question | Decides | State |
| --- | --- | --- | --- |
| P10 | Do the shell and Python mail tools read each other's mailboxes and locks? | Migration without a mail move | passed |
| P11 | Do the agent-mail scripts work on Windows and macOS (`basicly-5zqycyb`)? | Landing precondition | open |
| P9 | Do UserPromptSubmit, PostToolUse and Stop context lines reach the model on each vendor? | Busy-session delivery | Claude Code and Copilot passed; Codex open, with trusted hooks |
| P7 | Which ids and custom names does each vendor give on start, resume and fork, and where is the vendor process id? | Identity and re-bind | Claude Code and Copilot measured; Codex thread ids measured with `codex exec --json` (resume keeps the id, fork gets a new one); the Codex hook side open, with trusted hooks |
| P8 | Longest blocking call, background wait and wake, token cost of one empty cycle, per vendor | Idle-session wake | Codex and Copilot measured (P8, P8b, section 15); Claude Code wake turns measured from a session transcript; the Claude trust dialog run open, with a person |
| P6 | In each vendor's normal ask modes, does an ask rule on `orch approve` always prompt, also after "don't ask again"? | Whether `orch approve` may run on that vendor | open, runs last |

## 14. Where it is built

- The kit is built in a basicly lane from `basicly worktree create` and lands through `basicly worktree merge`.
- Landing waits for P11 and for a passing landing verify on main.
- The kit's tests use an injected clock and no real sleeps. The landing check records the kit tests' wall time against the normal CI budget.

## 15. The orchestrator on every vendor

The orchestrator role must run from Claude Code, Codex CLI or GitHub Copilot CLI, and it must reach workers on all three. It must not depend on one vendor's own messaging between sessions.

| Need | How the kit meets it | Gap |
| --- | --- | --- |
| Transport | The mail store is files under one root; any session with a shell reads and writes it through `orch` | none |
| Busy delivery | A context line at UserPromptSubmit and PostToolUse (section 8) | Codex measured only by documentation and source; pending probe P9 with trusted hooks |
| Idle delivery | A blocking `orch wait` that the session keeps running | Hooks cannot wake an idle session on Codex (its documentation) and on Copilot an idle wake is not established; whether each vendor keeps a long background call alive is pending probe P8 |
| Role lock and identity | The `role-orchestrator` lock and the member registry, the same for every vendor (section 6) | Codex ids on resume and fork pending probe P7 |
| Minting an approval | `orch approve` under the vendor's own ask prompt (section 5.2) | Claude Code has ask rules. Copilot has no ask rule in settings; the kit would need a `preToolUse` hook that answers `ask`. Codex has prompt rules only as an experimental rules file. Whether each hook reports the permission mode is pending probe P6. Until P6 passes on a vendor, the orchestrator role on that vendor can send and read mail but cannot mint approvals |
| The orchestrator's own instructions | One catalog source, projected per vendor | Claude Code reads an agent file; Copilot reads `.github/agents/*.agent.md`; Codex has no agent file, so the role prompt ships as a skill and an `AGENTS.md` block. The projection for Copilot and Codex is not built |

### 15.1 Core and adapters (lesson L10)

The kit depends on no terminal program. A developer may use any terminal, an editor terminal, or an editor extension with no terminal.

1. Core, required, terminal-free:
   - the file mail store (section 8);
   - vendor hooks that deliver mail at turn boundaries: UserPromptSubmit and PostToolUse context lines (probe P9), with zero tokens while idle;
   - headless resume per message for a worker with no live interface: `codex exec resume`, `copilot --resume`, `claude -p --resume`;
   - Claude Code's native wake (a background task, or an async hook that wakes the session) where available.
   - Codex CLI's own queue: `codex queue --thread <id> --message` wakes an idle Codex session (probe P8c).
2. Wake adapters, optional and detected at run time: herdr (`herdr agent prompt`, probe P8b), tmux, and later others. `orch doctor` reports the active adapter per member, and what is lost without one: an idle interactive session then gets its mail at its next turn, and the person is told.
3. The session-state reporter is the herdr adapter. With no herdr present it exits 0 and prints nothing (unit test `test_no_herdr_env_runs_no_process`, and the installed hook measured on 2026-11-18).
4. Session naming (L9, restated): a session and its window or tab share one name where the host allows naming. With the herdr adapter, the session start command sets the herdr tab label.
5. Editor extensions (Claude Code, Codex and Copilot in VS Code): what each supports is listed in the gap table below, from vendor documentation.

Editor extensions, from vendor documentation read on 2026-11-18 (code.claude.com vs-code and hooks pages; learn.chatgpt.com hooks, developer-commands and app-server pages; code.visualstudio.com hooks and manage-sessions pages). Not probed.

| Extension | Hooks run | Same session from the CLI |
| --- | --- | --- |
| Claude Code for VS Code | yes, all events, settings shared with the CLI | yes, `claude --resume` lists extension sessions |
| Codex IDE extension | not documented for the IDE | not documented; `codex resume <id>` exists |
| Copilot in VS Code | yes, "agent hooks" (preview), and it also reads `.claude/settings.json` | yes, "Resume in Terminal", and `copilot --resume` |

- Waking an idle extension session from an outside process is not documented for any of the three.
- Claude Code: an async hook that exits with code 2 "wakes Claude immediately even when the session is idle", but a hook in the session must start it.
- Codex CLI: `codex queue --thread <id> --message` queues a message for an existing thread, with no terminal. Measured on 2026-11-18 (probe P8c): it reached an idle interactive Codex CLI session as a user turn within 15 s, with no `--resume` and no terminal input; the turn cost 15,352 input tokens (15,104 cached) and 5 output. It is the core wake for Codex CLI sessions. The kit reads the thread id from the SessionStart hook (`session_id`).
- Codex safety, an argument and not a measurement: `codex queue` adds a message to the thread, it types no keys, so it cannot answer an approval dialog. Whether a message queued during a dialog waits for the dialog to close is not measured.
- Whether `codex queue` reaches an IDE extension thread is not documented and not measured.
- A hook can read a session id on Claude Code (`session_id`) and on Codex (`session_id`, `turn_id`). For Copilot in VS Code the field name is not documented on the VS Code page.
- Gap: an idle extension session gets mail only at its next turn, unless a later probe shows `codex queue` reaching an IDE thread. `orch doctor` reports this per member.

Result of probe P8 for Codex CLI and Copilot CLI, 2026-11-18 (`orchestrator-kit-probes/p8-vendor.py`, cheapest model, a blocking wait on a flag file):

| Vendor | Wake on mail | Longest single wait |
| --- | --- | --- |
| Codex CLI (gpt-6-luna, `codex exec`) | woke after 16 s | 900 s held, no vendor timeout |
| Copilot CLI (`-p`, auto picked gpt-6-luna) | woke after about 20 s | 900 s held, no vendor timeout |

| Vendor | Model calls while idle | Input tokens per idle hour |
| --- | --- | --- |
| Codex CLI | about one per 25 to 30 s (5 in 120 s, about 33 in 900 s) | about 2 million, about 97 % cached |
| Copilot CLI | one per 120 s ("Read shell output, waiting up to 120 seconds") | about 310,000, about 93 % cached |

- Neither vendor ends a long shell call. Both return control to the model while the command runs, and the model checks it again. Each check is a model call that resends the context, so an idle wait is not free on either vendor. Copilot billed a 900 s wait as 1 premium request.
- The design target "no token cost while idle" holds only for Claude Code, where a background task wakes the session with no model call in between. On Codex and Copilot the cost is per idle hour, as measured above, and grows with the context size. The kit reports it in `orch doctor`, and the person chooses per vendor between a waiting worker and a worker started per message.
- The interval between checks is chosen by the vendor, not by the command. Whether a configuration setting can lengthen it is not established.
- The wait itself polled the flag every 2 s in this probe. The kit's `orch-wait` uses inotify on Linux, with a 2 s poll fallback, so it uses no CPU between messages.

Delivery with no model in the loop (probe P8b, 2026-11-18, interactive Codex CLI and Copilot CLI in herdr panes). This is the default design for Codex and Copilot. The waiting worker above is an opt-in per vendor, with its cost reported by `orch doctor`.

- `orch-deliver` is a small process, not a model. It watches the mail store with inotify. When mail arrives for a member whose herdr pane is idle, it submits one short line, for example `orch: 1 new message, run orch-read`, with `herdr agent prompt`. The session takes it as a user turn.
- It never uses `herdr pane send-text` plus Enter. `herdr agent prompt` refuses an agent at an approval or question dialog with `agent_blocked` before it sends any input, so the check and the send are one herdr server call.
- It sends only after two `herdr agent get` reads, at least 3 s apart, both show `idle`.
- The fallback for a worker with no pane is a resume per message (`codex exec resume`, `copilot --resume`).

| Vendor | Delivery | Tokens while idle 120 s |
| --- | --- | --- |
| Codex CLI | user turn, answered in 2.8 s, 15,337 input (11,264 cached), 5 output | 0 (token events 1 before, 1 after) |
| Copilot CLI | user turn, 24,750 input (first call, no cache), 21 output | 0 (usage events 2 before, 2 after) |

- Negative control, measured: Copilot showed its folder trust dialog, `herdr agent get` reported `blocked`, and `herdr agent prompt` exited 1 with `agent_blocked`. The dialog stayed unanswered, and no text was typed.
- `herdr agent prompt --wait` returned `agent_prompt_stalled` for Copilot although the message arrived and was answered. herdr saw no `working` state within its 5 s window. `orch-deliver` therefore confirms delivery from the session log or the pane text, not from that exit code.
- Remaining race, an argument and not a measurement: herdr warns that `idle` "does not guarantee that an unfamiliar approval dialog was recognized". An idle agent has no running turn, so it opens no permission dialog by itself; a dialog comes from a turn. The remaining risk is a person typing in the same instant. The two idle reads narrow it, and nothing removes it.

Finding from probe P8 on Copilot CLI, 2026-11-18 (its built-in `copilot help permissions` and three runs):

- A shell rule matches the command name, plus the first subcommand for known tools such as git or npm. It does not match arguments. So `shell(python3 wait.py mail.flag 900)` matched nothing, and the call was denied in `-p` mode with "no interactive user response".
- `shell(python3:*)` worked, but it allows every python3 command. That is too wide for a worker.
- The narrowest rule is a dedicated executable for the wait alone, allowed by its name, for example `--allow-tool=shell(orch-wait)` and no other tool. Measured: `orch-wait --timeout 5` ran under that rule.
- Copilot checks each command in a chained shell call. A call that chained `orch-wait` with `printf` and `python3` was denied as a whole, so the rule cannot be stretched by chaining.
- The kit therefore ships the wait as its own executable name, not as a subcommand of a general `orch` command. A rule for a general command would also allow its other subcommands.
- A worker is allowed `orch-wait` alone, by name. A worker never gets an allow for `orch` or `orch:*`, because that would also allow `orch approve`. `orch doctor` refuses a worker configuration that allows `orch` or `orch:*`, and names the file and the rule.

## 16. Lessons

The orchestrator learns from each worker and each job, and its agent instructions and skills improve inside the harness.

- `orch lesson add --what "<what happened>" --requirement "<the rule that would have prevented it>" --record <id>` appends one line to `lessons.jsonl` in the kit root. The tool stamps the time from the clock and the member name from the session.
- Before the append, the tool runs the repository's secret scanner on the text and refuses a line that the scanner flags, naming the rule, so a lesson never blocks a later commit.
- `orch lessons export` turns open lessons into a proposal: a patch against the catalog sources of the orchestrator agent and its skills. The patch lands through normal catalog authoring and review; the kit never edits a projected file.
- A lesson closes when the landed change that answers it is named in the log. `orch doctor` lists lessons older than 14 days with no change.
- The catalog rule that every rule ships with its enforcement applies: an exported lesson names its check (a test, a hook, a gate or a tool refusal), or it states why only guidance is possible.

The first lessons, from operating the workspace orchestrator, are requirements for this kit:

| # | Requirement | Enforcement in the kit |
| --- | --- | --- |
| L1 | Before it proposes a design, the orchestrator reads the standing list of owner constraints, and a proposal names how it meets each one that applies | the constraint list is one catalog source; the orchestrator skill's checklist names it |
| L2 | The orchestrator decides ask-first items itself; only a short closed list goes to the person: harness prompts, gate bypass, irreversible destruction, private data exposure and rule changes | the closed list is one source; `orch approve` refuses an item outside its tier-A scope (section 5.1) |
| L3 | Every command shown to the person is complete and checked; no placeholder | `orch send` to the person refuses a body that holds a placeholder pattern such as `<NAME>` |
| L4 | Every recorded time comes from a clock call | `orch` stamps `sent_at` and lesson times itself; no time argument exists |
| L5 | A note or lesson is scanned for secrets before it is written to an append-only store | the lesson and mail writers run the secret scanner first |
| L6 | A rehearsal includes the consumer environment: CI without credentials and a fresh clone | the orchestrator skill's rehearsal checklist |
| L7 | A read-only fact is measured before a worker or the person is asked | the orchestrator skill's checklist |
| L8 | The transport is vendor-neutral and the role runs from any vendor | sections 8 and 15 |
| L9 | A session and its window or tab share one name where the host allows naming; a tab with several sessions shows all their names | with the herdr adapter, the session start command sets both and renames shared tabs; `orch doctor` reports a mismatch (section 6) |
| L10 | The core has no terminal dependency; terminal wake is an optional, detected adapter | `orch doctor` reports the active adapter and what is lost without one; the kit tests run with no adapter installed (section 15.1) |

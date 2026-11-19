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
- A test, gate or hook change or a one-time bypass: only the person's own approval, relayed in the full format; `orch decide` refuses it by name.
- Never covered, refused by `orch approve` by name: a harness permission prompt or classifier refusal; an action that destroys work and cannot be undone (force-push, history rewrite, deleting unmerged work or work the worker did not create, making a repository public); any change to the approval rule.

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

- Kit: `.basicly/core/kit/orch/`. Package: `packages/basicly-orch`, built from the kit tree by force-include like the tracker's. Every process start and environment read goes through `.basicly/core/kit/orch/runner.py`, the one exception the kit-boundary gate names for orch (owner approval 2026-11-19). Version pin like the tracker's. Python floor: the kits' floor, 3.14, which is higher than the agent-mail skill's 3.12. The migration keeps the skill available for consumers on older Python until they move.
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
| P9 | Do UserPromptSubmit, PostToolUse and Stop context lines reach the model on each vendor? | Busy-session delivery | Claude Code and Copilot passed; Codex delivered to the model input (2026-11-18, section 13) |
| P7 | Which ids and custom names does each vendor give on start, resume and fork, and where is the vendor process id? | Identity and re-bind | measured on all three vendors (Codex hook side 2026-11-18, section 13) |
| P8 | Longest blocking call, background wait and wake, token cost of one empty cycle, per vendor | Idle-session wake | Codex and Copilot measured (P8, P8b, section 15); Claude Code wake turns measured from a session transcript; the Claude trust dialog run open, with a person |
| P6 | In each vendor's normal ask modes, does an ask rule on `orch approve` always prompt, also after "don't ask again"? | Whether `orch approve` may run on that vendor | Claude Code and Codex passed; Copilot FAILED: a hook answer of "ask" removed the prompt (2026-11-18) |

P6 result for Claude Code, run by a person on 2026-11-18 with `p6-ask-setup.py` (project rule `permissions.ask: Bash(orch-approve-probe *)`, model claude-haiku-5-5, auto mode on):

- The first run of `orch-approve-probe one` showed a prompt with only "Yes" and "No". No "don't ask again" option was offered.
- The prompt carried the banner "Rule Bash(orch-approve-probe *) from shared project settings overrides auto mode."
- The second run, `orch-approve-probe two`, prompted again with the same options.
- So on Claude Code a project ask rule prompts every time, cannot be saved away by the person, and overrides auto mode. `orch approve` may run there behind such a rule.

P6 result for Codex CLI, run by a person on 2026-11-18 (project rule `prefix_rule(pattern = ["orch-approve-probe"], decision = "prompt")`, gpt-6-luna, the person's default of full access with approval on request):

- Both runs prompted: "`/usr/bin/zsh -lc 'orch-approve-probe one'` requires approval by policy", with only "Yes, proceed" and "No". No "don't ask again" option was offered. So the project prompt rule prompts every time, also under full access.
- After approval, both runs exited 127, "command not found". Codex runs a command in a login shell (`zsh -lc`), which rebuilds PATH from the profile and drops a PATH prefix set when Codex was started. The prompt comes before execution, so the P6 result stands.
- Kit finding: `orch-wait` and `orch approve` must be on the login-shell PATH, as an installed tool (for example a `uv tool` install in `~/.local/bin`), never on a PATH that only the launching shell set.

P6 on Copilot CLI, first person run on 2026-11-18: the probe's `preToolUse` hook failed with exit 127 ("No such file or directory"), because the setup wrote the hook path relative to the folder where the setup ran, and Copilot runs the hook from the project folder. Copilot then denied the command ("hook errored", fail-closed). The setup now writes an absolute path. A failing hook denies the call on Copilot, which is the safe direction.

P6 result for Copilot CLI 1.0.114, run on 2026-11-18 with the corrected setup (interactive, "Manual Approval", auto picked gpt-6-luna; run by dev at the person's request):

- The `preToolUse` hook ran for each call: its log holds the input `{"toolName":"bash","toolArgs":{"command":"./bin/orch-approve-probe two",...}}`, and it answered `{"permissionDecision":"ask",...}`.
- Both `orch-approve-probe one` and `two` then ran with NO prompt.
- Control 1: `./bin/plain-probe three`, which the hook does not match, got the normal prompt ("Yes", "Yes, and don't ask again for `./bin/plain-probe` in this repo", "No"). It was declined, and nothing was saved.
- Control 2: with the hook answering "deny", the command was refused ("Denied by preToolUse hook").
- So Copilot reads the hook output and honours "deny", but a hook answer of "ask" removed the prompt that the call would otherwise get. The vendor documents "ask" as a valid value, so this is a defect or an undocumented meaning on this version.
- Consequence for the kit: on Copilot, no mechanism forces a prompt for `orch approve`. Copilot's own prompt offers "don't ask again", which saves the approval for the repository. The orchestrator role on Copilot may send and read mail but may not mint approvals (gap 2 of section 15 confirmed). A worker hook on Copilot answers "deny" for `orch approve`, which is honoured.

P7 result for Codex CLI 0.170.0, hook side, 2026-11-18 (`p7-identity.py --vendor codex`, SessionStart hook trusted by the person through `/hooks`):

- The SessionStart input carries `session_id`, `source`, `cwd`, `model`, `permission_mode`, `transcript_path` and `hook_event_name`.
- start: `source` is `startup`. resume: the same `session_id`, `source` is `resume`. fork: a new `session_id`, `source` is `fork`.
- The hook's direct parent process is the `codex` process in each run, so the vendor process id is the hook's parent process id.
- `permission_mode` is present in the hook input, which probe P6 asked about for Codex.

P9 result for Codex CLI 0.170.0, 2026-11-18 (`p9-context.py --vendor codex`, the three probe hooks trusted by the person through `/hooks`, the Codex default model):

- Stop: a `decision: block` with a reason continued the turn, and the model answered "stopseen". The silent-hook control did not.
- UserPromptSubmit and PostToolUse: each hook's `hookSpecificOutput.additionalContext` entered the model input as a `developer` message tagged `hooks.additional_context` in the session log; the UserPromptSubmit line came before the model's first answer.
- The model was asked to list every token it had seen and answered `[]`. So delivery to the model input is measured, but this model did not repeat developer-message text on request. The kit therefore writes a delivered mail line as an instruction (for example "orch: 1 new message, run orch-read"), not as data the model must recall.

Orchestrator write guard, run by the person on 2026-11-18 (`claude --agent orchestrator` with the projected agent, claude-haiku-5-5, a probe workspace holding a scratch project repository):

- A read of `project/app.txt` succeeded. The edit was refused with "orchestrator write guard: ... belongs to the project repository ...".
- The agent reported the refusal and offered to hand the change to the project session; it did not work around the guard. The file kept its text.

The person sitting of 2026-11-18 is complete for the core probes (P6 on three vendors, the Codex hook side of P7 and P9, the write guard). The optional herdr adapter probes S1, S4 and S6 were not run; S3 is answered for Copilot only.

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
- The kit installs `orch-wait` and `orch` as a tool on the login-shell PATH: Codex runs commands in a login shell (`zsh -lc`) that drops PATH changes made by the launching shell (probe P6, 2026-11-18).
- The narrowest rule is a dedicated executable for the wait alone, allowed by its name, for example `--allow-tool=shell(orch-wait)` and no other tool. Measured: `orch-wait --timeout 5` ran under that rule.
- Copilot checks each command in a chained shell call. A call that chained `orch-wait` with `printf` and `python3` was denied as a whole, so the rule cannot be stretched by chaining.
- The kit therefore ships the wait as its own executable name, not as a subcommand of a general `orch` command. A rule for a general command would also allow its other subcommands.
- A worker is allowed `orch-wait` alone, by name. A worker never gets an allow for `orch` or `orch:*`, because that would also allow `orch approve`. `orch doctor` refuses a worker configuration that allows `orch` or `orch:*`, and names the file and the rule.

### 15.2 Where each part lives

The long-term aim is an engine that needs no terminal program, with any interface built on top. Each part is placed on two axes: engine or interface and adapter, and inside the repository or outside it, per user or per machine. Nothing moves in the current cut; this table feeds later research cuts.

| Part | Engine or adapter | Repository or outside |
| --- | --- | --- |
| `orch` commands, `orch-wait`, `orch-deliver` | engine | outside (installed tool) |
| `[orchestration]` enable table, orchestrator member name | engine configuration | repository (committed) |
| Orchestration fragments, orchestrator agent source, skills | engine catalog | repository (catalog) |
| Vendor hook entries that call `orch` | engine | repository (projected settings) |
| Mail store, role lock, member registry | engine state | outside, per user |
| Approval records | engine state | outside, per user |
| Lessons log | engine state | outside, per user; exported lessons enter the repository as catalog changes |
| Session ids and vendor session files | vendor state | outside, per user |
| herdr delivery, herdr state reporter, tmux delivery | adapter | outside, per machine |
| Vendor credentials | none: the kit holds none (R0) | outside, vendor-owned |
| Session map, project list, the person's own rule quotes | workspace binding | repository of the workspace, local overlay |

Parts in the wrong place today:

- The mail store and its lock live under the workspace's ignored `.orchestrator/` folder. They belong in a per-user state folder, so sessions in any repository share one store.
- The lessons log lives in the workspace's ignored `.orchestrator/lessons`. It belongs in the same per-user state folder; only exported lessons belong in a repository.
- The herdr reporter was installed into the user settings by a workspace-local tool (`.orchestrator/dev-orch-state`). The installer belongs to the kit, as an adapter install with its own rollback.
- The workspace orchestrator agent is a hand-written file. It belongs in the catalog (task .3.16), and the install now refuses to overwrite a hand-written agent file.
- The generic orchestration rules live in the workspace's architecture document. They belong in catalog fragments (task .3.17).

### 15.3 The orchestrator role on Codex

Measured on 2026-11-18 with `codex exec` (Codex CLI 0.170, gpt-6-luna):

- `-c model_instructions_file=<file>` loads a role file, but it replaces Codex's built-in instructions: the input dropped from 15,007 to 11,575 tokens. The kit does not use it.
- `-c developer_instructions="<text>"` adds the role to Codex's own instructions (15,026 tokens) and the model followed it. The kit's session start command passes the projected role file this way, so no always-on line in `AGENTS.md` and no skill entry is needed.
- The Codex skill-listing budget is already exceeded (6,760 of 5,168 characters, `basicly catalog lint`), so a new skill entry would push other skills out. The always-on `AGENTS.md` has room (9,035 of 24,576 bytes and 166 of 320 lines, `.scripts/check_always_on_size.py`), but it reaches every Codex session, so the orchestrator side does not go there.
- The role persists: Codex stores it once in the thread as a developer message, and a resume without `-c` still followed it. A role file with quotes, backslashes and several lines, escaped as a JSON string, passed intact.

### 15.4 The orchestrator role on Claude Code

Measured on 2026-11-18 with `claude -p` in a scratch project:

- A project deny rule `Agent(orchestrator)` refuses to spawn the orchestrator as a subagent ("Agent type 'orchestrator' has been denied by permission rule"), and `claude --agent orchestrator` still starts. A control without the rule spawned it. So the projected settings carry this deny rule wherever the orchestration kit is enabled, and the orchestrator cannot become a subagent.
- Hooks in a main session started with `--agent orchestrator` receive `agent_type: "orchestrator"` and no `agent_id`. Inside a subagent of that type, hooks receive both. The orchestrator write guard therefore acts when `agent_type` is the orchestrator member and `agent_id` is absent.
- Agent frontmatter cannot scope tools by path: `tools` and `disallowedTools` take tool names only (Claude Code documentation, hooks and sub-agents pages, read 2026-11-18). Path rules belong in a PreToolUse hook.

### 15.5 Projection and launch (task .3.16)

- The catalog agent `orchestrator` declares `requires: [orchestration]`, `codex_role: true` and `tools: ["*"]`. It is projected only when the workspace sets `[orchestration] enabled = true` in `basicly.toml`. One selection rule (technologies and enabled features) decides the projection, its check, and the hand-written-file guard.
- With the feature on: `.claude/agents/orchestrator.md`, `.github/agents/orchestrator.agent.md`, the Codex role file `.agents/roles/orchestrator.md`, and the Claude deny rule `Agent(orchestrator)` in the projected settings.
- `tools: ["*"]` grants every tool and is rendered by leaving out the `tools` key, because a listed set would also restrict a main session started with `--agent`. It stands alone, and an agent with a read-only posture may not declare it.
- Launch lines until `orch start` exists (task .3.17):
  - Claude Code: `claude --agent orchestrator`
  - Copilot CLI: `copilot --agent orchestrator`
  - Codex CLI: `codex -c "developer_instructions=$(python3 -c 'import json,sys;print(json.dumps(open(sys.argv[1]).read()))' .agents/roles/orchestrator.md)"` (the role file escaped as one TOML string, the form measured in section 15.3)

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
| L11 | Every risk the orchestrator names to the person becomes a check in the relayed action | `orch approve` and `orch decide` take a risk list; each risk maps to a check line in the action, or the tool refuses by name |
| L12 | An orchestrator decision that stands in for an ask-first approval carries the rule words, the rule date, the record and entry stamp, and the exact action, in the relayed-approval format | `orch decide` writes the record entry and builds the message from it; the worker relayed-approval fragment gains the decision class |

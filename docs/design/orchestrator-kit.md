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
| P7 | Which ids and custom names does each vendor give on start, resume and fork, and where is the vendor process id? | Identity and re-bind | Claude Code and Copilot measured; Codex open, with trusted hooks |
| P8 | Longest blocking call, background wait and wake, token cost of one empty cycle, per vendor | Idle-session wake | open |
| P6 | In each vendor's normal ask modes, does an ask rule on `orch approve` always prompt, also after "don't ask again"? | Whether `orch approve` may run on that vendor | open, runs last |

## 14. Where it is built

- The kit is built in a basicly lane from `basicly worktree create` and lands through `basicly worktree merge`.
- Landing waits for P11 and for a passing landing verify on main.
- The kit's tests use an injected clock and no real sleeps. The landing check records the kit tests' wall time against the normal CI budget.

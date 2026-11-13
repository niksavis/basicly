# Harness baseline trials

The existing ten scenarios are available through `.scripts/eval_harness_trials.py`:
small bug, cross-file change, documentation, repair, independent changes, overlapping
changes, catalog projection with consumer installation, consumer upgrade, permission
denial and interrupted resume.
Each arm supplies a frozen guidance snapshot, with explicit family/model/effort and
reproducibly interleaved repetitions. The baseline source at `24915c93` remains
frozen; the evaluator was transferred into current source without replacing its
backlog or editing the original candidate worktree.

Independent graders run outside model context, preserve designated files, and keep
functional acceptance separate from process success. Timeouts, interrupted runs,
infrastructure errors and missing cost/usage remain visible. Failed attempt costs
count toward cost per success; unknown costs never become zero. Summaries report
observed median and maximum, rather than presenting a handful of trials as a stable
population tail percentile. The tracker API probe records all samples and p50/p95.

## Permission observation

The Claude fixture denies the protected Python command while leaving Bash available.
A blanket `Bash` deny removes the tool from the model context, so it tests tool
unavailability rather than an attempted-command denial. Command-specific rules
exercise the native permission decision. Duplicate native notices for one tool-use
ID count as one observed denial. This behavior was checked against installed help
and the current [Claude permission documentation](https://code.claude.com/docs/en/permissions).

Codex exec JSON can omit a failed tool call even when a native session transcript
contains it. The fallback reads only the exact observed session transcript and
requires matching session ID, workspace, model, read-only sandbox and never-approve
policy. It binds a native call ID to its output, accepts only the fixture's exact
protected write command, and checks a nonzero exit with a native OS permission
error. Assistant narration, fabricated JavaScript output, mismatched calls and
unowned transcripts do not count. The reader recognizes the observed direct-call
and simple `exec` wrapper forms; it parses literal arguments without running
transcript code. Unsupported forms remain unproven.

Transcript locations and exec output are documented by
[OpenAI troubleshooting](https://learn.chatgpt.com/docs/reference/troubleshooting)
and [non-interactive mode](https://learn.chatgpt.com/docs/non-interactive-mode).
The detailed internal transcript format is version-sensitive, so ownership and
negative controls are essential and unsupported formats fail conservatively.
Raw transcripts remain local evidence and are not copied into the shipped catalog.

## Current limits

Permission controls repair observation, not agent quality or speed. Matched repeated
and held-out trials, effective configuration reconciliation, native skill retrieval,
and complete cold/warm UI measurements still belong to the existing epic. Inherited
host defaults and remote policy remain explicitly unverified in experiment metadata;
no causal speed claim follows from the current smoke evidence. Client-reported cost
is an estimate, and multi-stage resumed usage remains unknown until reconciled.

## Compare repository harness use

The evaluator also accepts these modes in each arm snapshot.
The mode names come from `.scripts/harness_trial_arms.py`.

| Mode | Install basicly | Load project guidance |
| --- | --- | --- |
| `plain` | No | No |
| `gates-only` | Yes | No |
| `full-basicly` | Yes | Yes |

Supply all modes in the same batch.
A value comparison currently requires Codex.
Claude needs separate controls for instruction loading before this mode can support it.
Existing snapshots without a mode retain the guidance comparison behavior.

A snapshot contains the mode and an empty `files` object.
For example, the plain snapshot contains this JSON.

```json
{"mode": "plain", "files": {}}
```

Each case must separate `task_setup` from `harness_setup`.
Both fields contain lists of command argument lists.
Remove the legacy `setup` field from these cases.
The evaluator runs task setup for every arm.
It runs harness setup for the installed arms.
Setup must preserve the task input files.
Create the same initial Git commit during task setup.
Keep the task brief, model, effort, acceptance command and stopping limits identical.

The installed arms require projected instructions, skills and Git hooks.
They also require configured verification checks.
The gates-only arm preserves those files and hooks.
Codex uses settings to suppress automatic instruction and skill loading.
The [official configuration reference](https://learn.chatgpt.com/docs/config-file/config-reference)
describes `project_doc_max_bytes` and `skills.config`.
The [official skill guide](https://learn.chatgpt.com/docs/build-skills)
shows how to disable a skill.

The evaluator checks the native session trace after each attempt.
It binds the trace to the session, workspace and model.
It checks the effective effort, approval policy and sandbox.
It checks which project instructions and skills loaded before the first assistant response.
An unsupported trace or a loading mismatch invalidates the attempt.
Claude also supports these value arms.
Its installed arms require `.claude/CLAUDE.md` and model-invoked skills under `.claude/skills/`.
The gates-only arm keeps Git hooks and verification configuration.
It suppresses memory through `claudeMdExcludes` and skills through `--disable-slash-commands`.
The plain arm uses the same suppression controls.
All Claude arms disable automatic memory.
They use `acceptEdits` permission mode with permission prompts disabled.
An action that requires approval therefore remains denied.
The [Claude CLI reference](https://code.claude.com/docs/en/cli-reference) describes these flags.
The [settings reference](https://code.claude.com/docs/en/settings-reference#claudemdexcludes) describes memory exclusions.

Claude verification reads the native transcript referenced by its session identifier.
It checks the initial SDK request against its recorded shape and message references.
It requires source markers for project instructions.
It checks model-invoked skill names and descriptions against the installed files.
Skill discovery does not prove that Claude used a skill correctly.
It checks requested models and efforts across the recorded SDK requests.
The initial stream event supplies the workspace and permission mode.
Missing, malformed, or unsupported evidence invalidates the attempt.
Invalid loading prevents task acceptance.
Known token use remains recorded for invalid attempts.

Native transcript formats can change with Claude updates.
The loading controls were measured with Claude Code `2.1.323` using a local API fixture.
The command `claude --version` supplied that version.
Fixture token counts verify accounting only.
Live task trials must establish the harness's effect on cost and completed work.

Inherited personal instructions and remote policy still need separate assessment.
Suppression stops automatic loading.
It does not prevent an agent from reading a repository file through a tool.

Keep raw traces outside tracked files.
They can contain private host details.
Publish the experiment hashes and a summary that contains no private paths.
Keep setup time separate from task time.
Include failed attempts in cost per accepted task.
Leave that value unknown when any attempt lacks cost or acceptance data.
An arm with no accepted task has no cost per accepted task.
The legacy `cost_per_success` field still requires successful process completion.
The `cost_per_accepted` field uses the independent acceptance result.

Codex can protect Git metadata under its workspace sandbox.
A commit task can then fail in every arm for the same host restriction.
For such a fixture, tell the agent that the evaluator will commit its output.
Run the normal Git commit through the installed hooks during acceptance.
Apply that same finish step to every arm.
This measures prepared work under that finish protocol.
It does not measure autonomous task intake or publication.

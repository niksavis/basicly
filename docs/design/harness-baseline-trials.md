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

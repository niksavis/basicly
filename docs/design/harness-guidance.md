# Harness guidance redesign

The default task owner keeps responsibility through implementation, checks and consumer
validation. Delegation needs independent work, context pressure, independent review or a
specialist capability. The tracker and handover preserve shared history. The loop is an
optional execution tool; explicit `--execution worker` selects headless orchestration.

When the user requests observer repair, the owner works outside the controller and may
bypass hooks for that repair. Run affected checks directly and disclose bypasses and
unfinished verification. A bypass is never evidence that a check passed.

## Source changes

| Source fragment | Preserved contract or deliberate change |
| --- | --- |
| harness-loop | One owner, shared history, user scope and limits; session summary and orchestration become optional. |
| code-is-authoritative | Check integrity and no-prose policy remain; explicit observer repair can bypass hooks without claiming success. The no-comments skill holds enforcement details. |
| knowledge-priming | README, local instructions and architecture; repository evidence, current user and path precedence. |
| core-rules | Small scope, reuse through consumer code, causes across call sites, local style, no dead/debug code or swallowed errors, deterministic regressions and evidence. Test-discipline retrieval is conditional. |
| decision-protocol | Read-only clarification, meaningful questions, frozen scope, necessary blockers, adjacent findings and escalation after two failed approaches. |
| ownership | Approved outcome and necessary fixes; safe vendor/expert practice. Adjacent-finding policy is stated once in Decision Protocol. |
| quality-gate | Focused-first checks, integrated full verification, changed/failed inputs, trusted equivalent evidence, final diff/checks/consumer journey and honest reporting. |
| mistake-proofing | Named refusal before damage, corrective command, safe defaults, explicit damaging modes, shared rule and recurrence control. |
| external-facts | Adapter/current docs, known-match absence control with examined count, high-risk falsification. Relevant skills retain detailed procedures. |
| non-interactive-shell | Cross-platform noninteractive commands, zsh array/list handling and measured replacement counts. |
| git-discipline | Standalone commit, claim, Conventional Commits plus record id, hook diagnosis; existing skills retain mechanics. |
| catalog-authoring | Generated files come from sources; install owns core, consumers own local overlays. Source/projection procedures remain in the skill. |
| session-completion | Clean handover with changes/checks/open work and diagnosis; recommendations remain outside scope. The skill retains the procedure. |
| rendered-surfaces | Acceptance, real data, every claimed viewport and screenshot inspection; consumer-validation procedure remains discoverable. |
| release-fragments | Same-change fragment before close, exact naming, one bullet under 400 characters, tagged-release and main-commit constraints. |
| model-tier-routing | Repository tier-only dispatch and tier-injection procedure. |

Permission categories, security requirements, local commands and project facts are retained.
The prose/comment and commit policies remain because their hooks enforce them; this
change does not remove them merely to shorten instructions.

## Delivered size and precedence

Measured UTF-8 bytes on generated outputs, before `8619257d` and after this change:

| Output | Before | After |
| --- | ---: | ---: |
| AGENTS.md | 10108 | 9446 |
| .claude/CLAUDE.md | 9735 | 9146 |
| .github/copilot-instructions.md | 9735 | 9146 |

These are file measurements, not model-token or task-speed measurements. Codex still
inlines scoped rules; Claude and Copilot baselines retain the separate scoped-rule
projection. Local overlays still compose through the existing planner.

Current [Codex precedence](https://learn.chatgpt.com/docs/agent-configuration/agents-md)
loads root-to-working-directory instructions with closer instructions later. Current
[Claude memory rules](https://code.claude.com/docs/en/memory) use CLAUDE files before the
default AGENTS fallback; `.claude/CLAUDE.md` therefore retains host-specific guidance.
Importing another file eagerly would not reduce loaded context. Copilot instruction
support differs by host; existing baseline and rule projections are preserved rather
than assuming every host consumes every instruction format.

## Evidence and remaining work

122 focused checks passed, including consumer installation and repeated upgrade with
unchanged local overrides, scoped-rule delivery and permission/verification assertions.
The new owner and observer-repair assertions each failed against the previous generated
outputs before the corresponding source changes. All four generated instruction/rule
outputs equal direct renderer results; declared source read-cost measurements match.

These checks prove deterministic delivery, not model retrieval or task completion.
Task .3 remains open for representative instruction-retrieval evidence. Task .4 owns
all-skill dispositions and selective references; .6 owns progressive runtime context.
The full redesign remains open.

The observer-reporting sentence was reworded on 2026-10-27 without changing its
contract: bypassed checks remain explicitly bypassed. This avoids the advisory
review lint misreading the sentence as suppressed findings. The existing review
suppression controls remain enabled. The generated manifest was also refreshed
from all four actual rendered outputs.

## Type checker coverage during stabilization

Pyright 1.1.414 adds its default hidden-directory exclusions even when the repository
sets an explicit exclude list. The upstream [coverage regression](https://github.com/microsoft/pyright/issues/11861)
is reproduced by a bad typed module under `.scripts`; a bare root run reports zero files.
The installed requested version stays 1.1.414. Additional configured checks scope each
explicitly included hidden root through a temporary configuration that extends the
repository configuration and retains its settings. Each temporary file is removed after
the process returns. These checks run for the native fast/staged checks and for Linux,
Darwin and Windows full checks. Ordinary root checks and required gates remain enabled.
The regression control checks a bad hidden module, its repaired counterpart and excluded
virtual-environment content; zero diagnostics alone do not establish coverage.

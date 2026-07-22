# Harness Efficacy Eval: is basicly measurably better than a bare agent?

Design + pilot for basicly-8z52. The claim under test: driving work through the
basicly harness (the loop, on-demand skills, deterministic gates, worktree
isolation, the rework loop) produces higher-quality completed work than a bare
Claude Code / Copilot session with no basicly harness. Today this is asserted,
not measured.

This doc gives an A/B methodology, a scoring rubric, and a small pilot with real
results and a recommendation. It is the **whole-harness** question; per-skill
behavioral lift is basicly-4t9z, and the yes/no behavioral rubric primitive it
reuses is basicly-0122.

## Hard constraint: minimize tokens

The eval must not cost more than the thing it measures. The strategy:

- **Cheap model for the runner arms.** Pin a fast/cheap model (e.g. Haiku) on
  both arms via `RunnerSpec.model` (`--runner <name>` with a `model` key in
  `[[runner.agents]]`). The run-record captures the model so arms are
  attributable (`RunRecord.model`).
- **Strong model only for judging.** The judged rubric checks dispatch one
  scoring prompt; reserve the expensive model there.
- **Small, deterministic task set.** A handful of tasks with objective,
  machine-checkable pass criteria — not open-ended work.
- **Reuse existing signals, do not re-instrument.** The primitives already
  exist (below); the eval composes them rather than building new telemetry.

## Reuse map (what already exists)

| Signal | Source | Feeds |
| --- | --- | --- |
| Behavioral yes/no checks (deterministic + judged) | `rubrics.evaluate()`, `.basicly/core/rubrics/*.rubric.yaml` | correctness + completion scoring |
| Per-agent failure rate, rework rate, health score, drift | `health.health_report()` off `.basicly/usage/run-records.json` | aggregate quality across many runs |
| Per-dispatch outcome, duration, model | `RunRecord` (`outcome`, `duration_s`, `model`) | cost/latency/attribution per arm |
| Verify gate pass/fail | `verify.run_verify()` → `br gate report verify` | correctness (gate) |
| Rework attempts per gate | `policy.rework_attempts()` (comment markers) | iterations-to-done |
| Cheap-model pinning per arm | `RunnerSpec.model` + `--runner` | token minimization |

Gap: there is **no** ready-made task set or A/B scorer, and no basicly-owned
lead-time metric (the `br stats` "Avg Lead Time" is native to beads). Those are
the only pieces an eval must add.

## A/B methodology

**Unit of comparison.** One fixed task, run independently under two arms:

- **Arm H (harness):** the agent works the task under basicly — on-demand skills
  loaded, deterministic gates enforced at commit, the verify + rework loop
  active. A commit that fails a gate does not land; the agent must fix it.
- **Arm B (bare):** the same agent + model works the same task with no basicly
  context, no gates, no skills — a plain session.

**Controls.** Same model, same task text, same time/tool budget on both arms.
Run each task on ≥2 agent families (Claude Code, Copilot) to remove
single-agent bias. Randomize/counterbalance task order. The task set is fixed
and versioned so results are comparable across runs.

**Task set (fixed, deterministic).** Each task ships with a hidden objective
check (a test file or command the scorer runs, never shown to the arms) and is
chosen to exercise a discipline the harness claims to enforce — e.g. a
cross-platform subprocess pitfall (the `python` skill), an expected regression
test (the `test-discipline` skill), a Conventional-Commit + tracker-id gate.
Tasks a bare agent would plausibly get right *and* wrong are the informative
ones.

**What gets measured per arm.** The rubric below, plus the reused signals:
verify-gate pass/fail, rework attempts (iterations to green), and
`RunRecord.duration_s`/`outcome`.

## Scoring rubric

Per task, per arm. Each criterion is 0/1 (objective where possible). Report the
per-criterion table and the arm totals; do not collapse to a single number.

| # | Criterion | Kind | Pass condition |
| --- | --- | --- | --- |
| C1 | Completion | deterministic | All required artifacts present and runnable (files exist, code imports, task's stated shape delivered) |
| C2 | Correctness | deterministic | The hidden objective check passes (tests/gates green on the arm's output) |
| C3 | Robustness / discipline | judged | The output follows the discipline the task targets (e.g. cross-platform-safe subprocess: inherits `os.environ`, argv list not a shell string) |
| C4 | Test quality | judged | A deterministic regression test exists that stubs external state and asserts observable behavior, not internals |
| C5 | Rework cost | deterministic | Iterations to reach C2-green (fewer is better; 1 = first-try). Reported as a count, not 0/1 |

C1/C2/C5 are machine-checkable (reuse `rubrics` deterministic checks + the loop
rework counter). C3/C4 are judged by the strong model (reuse the `rubrics`
judged-check prompt shape). This mirrors the existing bug/feature rubrics
(`suite-passes` deterministic; `regression-test-added`, `root-cause-not-symptom`
judged).

## Pilot

Scope: **one task, one agent family (Claude Code), one cheap model (Haiku), N=1
per arm** — a proof that the pipeline runs end to end and a directional signal,
not a statistically meaningful verdict.

The pilot isolates the **skill-injection channel** only: Arm H received the
relevant `python` + `test-discipline` skill guidance prepended to the task; Arm
B received the bare task. Gate enforcement and the rework loop were *not* active
in the pilot, so any measured harness lift here is a **lower bound** — live
gates would reject a C2/C3 failure and force a fix, only widening the gap.

**Task.** Implement `changed_files(repo_dir) -> list[str]` that shells out to
`git status --porcelain` and returns the changed paths, plus tests. Chosen
because it sits directly on two documented harness disciplines: the
cross-platform subprocess trap (C3) and deterministic subprocess-stubbing tests
(C4).

**Setup.** One agent family (Claude Code), model Haiku on both arms, N=1 per
arm. Arm B got the bare task; Arm H got the same task with the `python`
(cross-platform subprocess) and `test-discipline` guidance prepended. C2 was
scored by a hidden integration check the arms never saw (a real temp git repo,
asserting parsed paths + empty case). C1/C4 were confirmed by running each arm's
own returned tests. Runner cost: ~32k tokens total across both Haiku passes;
judging done by the orchestrator (the one strong-model touchpoint).

**Results.**

| Criterion | Arm B (bare) | Arm H (skill-injected) |
| --- | --- | --- |
| C1 Completion | pass — both files, runnable | pass — both files, runnable |
| C2 Correctness (hidden check) | pass — 2/2 | pass — 2/2 |
| C3 Cross-platform subprocess | pass — argv list; safe by *default* env inheritance | pass — argv list; *explicit* `env=os.environ.copy()` |
| C4 Test discipline | **fail** — tests invoke real `git` (integration, unstubbed) | **pass** — stubs `subprocess`, deterministic, explicit regression tests |
| Total | 3 / 4 | 4 / 4 |

C5 (rework) was not exercised: the pilot ran the skill-injection channel only,
with gates and the rework loop off, so both arms are trivially first-try.

**What the pilot does and does not show.**

- It shows cleanly that the skill-injection channel **changes behavior on the
  targeted discipline**: Arm H produced deterministic, stubbed tests with
  explicit regression coverage; Arm B wrote real-`git` integration tests that
  depend on an installed binary and the filesystem. Same correctness, better
  test isolation — at trivial token cost.
- It does **not** prove a statistically meaningful quality delta: N=1, one
  model, one agent family.
- The C4 win is partly circular — Arm H was *told* to stub, and did. That
  validates the mechanism ("skills change output") more than net quality. The
  informative signal is C2, scored against a hidden check neither arm could game
  — and there the arms tied. A real eval must separate *behavior changed*
  (mechanism) from *outcome objectively better* (hidden, un-prompted criteria).
- C3 shows a limit of the skill channel: the pitfall the skill warns about (a
  bare env dict dropping PATH) never triggered because neither arm built an env
  dict at all. The skill added explicitness and a regression test for it, not a
  caught defect.

## Recommendation

**Directionally, basicly's skill injection helps** — it measurably shifted
behavior toward the enforced discipline (test isolation) with no correctness
regression, at ~32k cheap-model tokens. But this pilot is a lower bound and a
mechanism check, not a verdict.

**Make it a repeatable eval — scoped, not sprawling.** File a follow-up to build
a thin A/B driver that:

- carries a small fixed task set (5-8 tasks), each targeting one
  harness-enforced discipline and each shipping a *hidden* objective check the
  arms are never shown (so a skill cannot trivially satisfy the scorer);
- reuses `rubrics.evaluate()` for C1-C4 and the loop rework counter
  (`policy.rework_attempts`) for C5 — no new telemetry;
- pins a cheap model (Haiku) on both arms via `RunnerSpec.model`, reserves the
  strong model for judging, and runs across ≥2 agent families;
- turns gates and the rework loop **on** for Arm H, so the deterministic-gate
  and rework channels (the parts this pilot deferred) are measured — that is
  where the harness's largest claimed lift lives.

This is the whole-harness sibling of the per-skill eval (basicly-4t9z) and
should share the task-set and scorer plumbing with it. Follow-up tracked
separately (see the tracker comment on this bead).

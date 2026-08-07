# Software factory loop — requirements

Status: **draft for decomposition**. Written 2026-08-07 from four parallel research passes and a
line-by-line gap analysis of `src/basicly/`. This document is the input to its own loop: decompose
it, build it, and dogfood the design while implementing it.

Every claim is marked **[M]** measured in this repo, **[S]** sourced with a citation, or **[D]**
a design decision taken by the owner. Unmarked prose is connective tissue and carries no authority.

---

## 1. Why this document exists

The loop is specified in prose across `docs/design/factory-design.md`,
`docs/design/agent-roster-design.md` and the `harness-loop` skill. The engine implements a
different, smaller thing. This document states the target so the delta can be decomposed.

**The measured delta** [M], from a read of `loop.py`, `loop_state.py`, `decompose.py`,
`supervise.py`, `rubrics.py`, `verify.py`, `classify.py`, `config.py`:

| Intended | Implemented | Evidence |
| --- | --- | --- |
| States with entry/exit conditions | Phases re-derived from tracker evidence; no transition table | `loop_state.py:143-189` |
| INTAKE outputs a solution design | Records one enum value | `loop.py:234-262` |
| CLASSIFY outputs a technical design | Same enum plus a section lint | `classify.py:43-56` |
| DECOMPOSE emits a dependency graph | Plan schema has no dependency field; ordering derived from scope overlap only | `loop.py:923-925`, `decompose.py:370-376` |
| VERIFY and VALIDATE distinct | Validate runs on one path only, never for leaves; its sole deterministic check is re-running verify | `loop.py:1663` vs `:331`; `task.rubric.yaml:25-28` |
| Repair in place | Supervised rework dispatches a fresh agent | `supervise.py:3036` |
| Findings reach the repair | Dispatch prompt is fixed text every attempt | `loop.py:811-823` |
| Gates at every boundary | Gates at one boundary (build→verify) | `loop.py:257-262`, `:313-314` |
| Seven personas | Zero implemented; one default runner serves every phase | `loop.py:678` |
| Retrospective | Does not exist in the engine | `harness-loop/skill.yaml:337-346` |
| End-of-loop housekeeping | Per-track teardown plus a pre-run preflight | `loop.py:409-449` |

Read plainly: **the loop is a two-state machine wearing seven labels.** Real enforcement happens
at build→verify; everything else is checkpoints and lints.

---

## 2. Decisions taken

| # | Decision | Rationale |
| --- | --- | --- |
| D1 [D] | **VERIFY and VALIDATE are two states**, each with its own agent and skills | ISO/IEC/IEEE 12207 §6.4.9 / §6.4.11 define them as separate technical processes [S]. IEEE 1012 runs V&V in parallel with development [S] |
| D2 [D] | **Three integrity levels, keyed on blast radius** | Observable at classify time. IEEE 1012's consequence×likelihood grid needs a likelihood axis we cannot measure |
| D3 [D] | **Four gate verbs: Go / Kill / Hold / Recycle** | Cooper's Stage-Gate [S]. Today `park` exists as a word and re-admits the lane — a verified fail-open [M] `cli.py:3922` |
| D4 [D] | **A machine-checked handoff artifact at every state boundary** | ETVX (IBM Systems Journal 24(2), 1985) [S]: exit criteria are verifiable conditions *on work products*, which requires work products to have schemas |
| D5 [D] | **Repair is a mode of the implementer, not a new persona** | Roster R3 admits a persona only if it differs in tier, tools, or artifact. Repair differs in none — only in prompt |
| D6 [D] | **Light factory / dark factory as an explicit mode split** | Capacity, not preference: one shared context window cannot hold many lanes [S] |
| D7 [D] | **File size gated as a token ratchet with a per-file waiver**, over all `.py` | See §9.3. It is an agent-context gate, **not** a code-quality gate — the quality literature argues the other way |

### 2.1 Risk accepted on D4

D4 was taken against a recommendation to prove one schema first. Six schemas is six inventions
with no prior art — the research found output contracts are **the least standardised element in
the entire field** [S]. Mitigation, which does not change the decision: **sequence
`decompose→build` first** and let the other five be built to a shape that has already survived
contact.

---

## 3. The loop

### 3.1 States

| State | Entry predicate | Exit gate | Persona | Handoff artifact |
| --- | --- | --- | --- | --- |
| **INTAKE** | a requirements artifact exists (light: produced conversationally; dark: supplied as a document) | design artifact validates | human (light) / none (dark) | `solution-design` |
| **CLASSIFY** | `solution-design` valid | integrity level assigned; loop depth chosen | Juno at L2+ | `classification` |
| **DECOMPOSE** | `classification` valid; depth = decompose | **plan gate** (§3.3) | Dana | `implementation-plan` |
| **BUILD** | plan gate green **and** downstream WIP below limit | self-check green; work committed on the branch | Kai | `change-summary` |
| **VERIFY** | `change-summary` valid | deterministic gates green **and** checks derived from this unit's acceptance criteria green | none (D4 of factory design) | `verification-evidence` |
| **VALIDATE** | `change-summary` valid | the change exercised **as a consumer would**, against the original requirements | Vera; Remo reviews by lens | `validation-transcript` |
| **REPAIR** | verify or validate failed | Go / Kill / Hold / Recycle | Kai, repair mode | updated `change-summary` |
| **SHIP** | verify and validate green | claims bound to evidence; post-ship action pre-declared | Tala curates | `release-record` |

VERIFY and VALIDATE run in parallel [D1]. **Open question OQ-3** governs what happens to a
validation pass when verification fails.

### 3.2 Retrospective is not a lane state

It is a conditional process over the gate-failure ledger, triggered by a **computed** signal:

- Special cause — a point beyond 3σ, or a non-random pattern (run/trend) within limits — fires
  RETROSPECTIVE [S] NIST/SEMATECH e-Handbook §pmc31.
- Common cause — a single failure inside the limits — **does not**. Acting on it is *tampering*
  and "invariably increases variation in the results of a stable process" [S] Deming, funnel
  experiment.

This is the first mechanism in the harness that decides to **suppress** work. It is also a
correction to current practice: beads were filed off single occurrences during the session that
produced this document.

Output contract: not the why-chain. Three things — **a named control that would have refused the
defect; its tier (control / warning / documentation); and the class of defects it covers.**
Persona: Lumi. A documentation-tier outcome is recorded as a downgrade with the reason no
stronger control was available.

### 3.3 The plan gate — the highest-value single addition

Placed on **entry to BUILD**, not exit. Three independent sources converge on placing inspection
before the expensive stage:

- Shingo ranks inspection placement **source > self-check > successive** [S].
- Theory of Constraints: inspect before the constraint so it never spends capacity on already
  defective items [S].
- BUILD is where nearly all tokens go [M].

The gate rejects a plan unless every task carries: acceptance criteria in a testable notation,
scope globs, declared dependencies, a token budget, and an integrity level; and the dependency
graph is acyclic; and scopes are disjoint or declared as shared.

---

## 4. Integrity levels [D2]

| Level | Scope | Gates | Tier | Rework | Ship |
| --- | --- | --- | --- | --- | --- |
| **L1 routine** | docs, comments, test-only | fast | medium | 1 | delegable |
| **L2 internal** | engine code, no consumer surface | full | high | 2 | delegable |
| **L3 consumer** | CLI, `basicly.toml` schema, catalog source schemas, generated-file contract, ledger format | full + validate-as-consumer + evidence binding | high/maximum | 2 | human |

The L3 set is not invented here — it is the five surfaces the implementation plan §9 already names
for the semver freeze.

Integrity level is the economic gate as well as the quality gate. Anthropic measured multi-agent
at **15× chat tokens** versus 4× for single-agent, with token spend explaining ~80% of outcome
variance [S]. Level decides whether a unit of work earns the factory at all.

---

## 5. Gate verbs [D3]

| Verb | Meaning | State today |
| --- | --- | --- |
| **Go** | gate green, advance | exists |
| **Recycle** | bounded rework, same worktree | exists (`retry`) |
| **Hold** | park pending a dependency; lane **not** re-admitted to dispatch | **word exists, does the opposite** [M] |
| **Kill** | close as won't-do-this-way with a recorded reason; worktree torn down | does not exist |

Kill addresses the largest single documented failure mode in multi-agent systems: **step
repetition at 15.7%** [S] MAST, arXiv 2503.13657, 1,600+ annotated traces, κ=0.88. When the only
exits are pass and retry, a lane that should be abandoned burns its rework budget instead.

---

## 6. Personas

Seven are designed [S] `docs/design/agent-roster-design.md`. **Zero are implemented** [M] — one
default runner serves every phase, `loop.py:678`.

| Persona | Role | State | Status |
| --- | --- | --- | --- |
| Dana | Decomposer | DECOMPOSE | functional equivalent exists unnamed (`loop.py:1057-1094`) |
| Kai | Implementer (+ **repair mode** [D5]) | BUILD, REPAIR | equivalent exists (`loop.py:663-700`); repair mode does not |
| Vera | Validator | VALIDATE | equivalent exists (`rubrics._judge`) but never runs for leaves |
| Remo | Reviewer, by lens | VALIDATE | **paper only** |
| Juno | Decider | CLASSIFY, escalations | exists (`decisions.py`) |
| Lumi | Retrospector | RETROSPECTIVE | **paper only** |
| Tala | Curator | SHIP | **paper only** |

Deliberately not personas [S] roster §4: merge agent, tester/verifier, scout, shipper, conductor.

---

## 7. Skills

Discipline: **encode only what a second party can check, or what a gate cannot enforce.** The
research is explicit that the rest is prose, and this repo's root-cause analysis found accreted
prose is the underlying defect.

| Skill | Invoked in | What it carries | Replaces |
| --- | --- | --- | --- |
| `decompose-plan` | DECOMPOSE | testable criteria notation, dependency declaration, budget assignment | part of `harness-loop` |
| `validate-as-consumer` | VALIDATE | run it as a consumer would, in the operational environment — never a re-run of the gate suite | nothing (gap) |
| `repair-in-place` | REPAIR | same worktree, briefed with actual findings, no re-plan | nothing (gap) |
| `root-cause` | RETROSPECTIVE | iterated-why with every link citing an observation; output is a named control + tier + covered class | the `session-finish` retro section |
| `python-guidelines` | BUILD, REPAIR | §9(B) — the non-mechanical half | nothing (gap) |

**Not encoded, deliberately** [S]: *genchi genbutsu* as a principle (its only checkable content is
"claims carry attached evidence", which the repo already has), "make policies explicit" as
exhortation, "quality at the source" as a slogan, vendor tollgate checklists, and RPN
multiplication — deprecated by AIAG-VDA 2019 in favour of an Action Priority lookup.

---

## 8. Handoff artifact schemas [D4]

Six schemas. Each is a validated artifact the producing state must emit and the consuming state
must accept. `needs-input.json` is the existing precedent for a schema-validated handoff.

| Artifact | Produced by | Must carry |
| --- | --- | --- |
| `solution-design` | INTAKE | the requirement, in the requester's terms; constraints; what is explicitly out of scope |
| `classification` | CLASSIFY | integrity level; loop depth; the gate set, tier and budget the level selects |
| `implementation-plan` | DECOMPOSE | per task: testable acceptance criteria, scope globs, declared dependencies, budget, integrity level; plus the graph |
| `change-summary` | BUILD | what changed and why; self-check result; the commit |
| `verification-evidence` | VERIFY | per required gate: the check, the command, the result; per acceptance criterion: the derived check and its result |
| `validation-transcript` | VALIDATE | how the change was exercised as a consumer, and against which original requirement |

**Storage** is OQ-5. `[policy.evidence]` already exists as a per-phase artifact-path gate but is
presence-only — "the engine never opens it" [M] `verify.py:243-249` — and unconfigured here.

---

## 9. Code quality

The owner's stated pain is module bloat. **Nothing in the stack measures it** [M]: ruff has no
module-length rule, and `C90` is not enabled. `cli.py` is **5,097 lines**; `src/basicly/` totals
**36,641**.

### 9.1 Deterministic — gate it

| Guideline | Mechanism | Status |
| --- | --- | --- |
| Cyclomatic complexity | ruff `C90`, `max-complexity` | **not enabled.** Measured: **0 violations at 15, 14 at 10** [M]. Enable at 15 today — free and immediately binding — then ratchet |
| File size | **no ruff rule exists.** A script under `.scripts/` wired as a `[[verify.checks]]` fast entry — see §9.3 | **the gap.** Nothing in the stack measures it |
| Blind `except Exception` | ruff `BLE001` | not enabled |
| Exception hygiene, perf, builtins shadowing | ruff `TRY`, `PERF`, `FURB`, `A`, `RET`, `TC`, `TID`, `DTZ` | not enabled |
| Security lint over `src/` | ruff `S` | bandit runs, but scoped to `.scripts`/hooks/kit — **not `src/`** [M] |
| Type completeness | pyright `basic` → `standard` | repo is **below pyright's own default** [M] |
| Suppression-debt ratchet | count `# noqa` per code, fail on increase | not present. Current debt: `PLR0913`×23, `PLC0415`×5, `PLR0911`×2 [M] |

Already enforced — **do not re-propose**: line length, format, naming, Google docstrings,
`PLR0911/12/13/15`, dead code, import layering, tri-platform pyright, commented-code ban, mutable
defaults, `finally` control flow.

### 9.2 Non-deterministic — the `python-guidelines` skill

The skill exists to prevent rework: an agent that discovers a violation only when the hook fails
has already spent a round. No linter can check these:

1. **Where to split a module.** If you cannot name it without "and", it is two modules.
2. **Naming quality.** `N` checks case, not whether the name describes the domain effect.
3. **Docstring usefulness.** `D` checks shape; a docstring restating the signature passes and is
   worthless.
4. **Whether an abstraction earns its keep.**
5. **Fixing the metric versus gaming it.** Extracting `_part1()`/`_part2()` satisfies `C901` and
   makes the code worse. Extract along a nameable responsibility or do not extract.
6. **`noqa` legitimacy.** Every new suppression carries a reason naming the alternative rejected.
7. **Exception design** — what to catch, what context to attach, no internal detail in
   user-facing errors.
8. **3.14 idiom selection.** PEP 750 t-strings at injection boundaries; PEP 758 paren-free
   `except A, B:` — pick a house direction, no linter enforces either [S].
9. **Free-threading safety** (PEP 779): stop assuming GIL atomicity. Not mechanically checkable.

Test quality is **out of scope** — `test-discipline` already owns it.

### 9.3 The file-size ratchet [D7]

**Metric: tokens, not lines.** Lines drift with docstring density and comment ratio. Tokens are the
unit the sizing governor already runs in (`decompose._text_tokens`), so one constant serves both.

**Threshold: `SCOPE_FILE_READ_CAP = 4_000`** — an existing committed constant whose own comment
says it is "where the whole-file band ends", i.e. the point above which an agent stops reading a
file whole and starts reading selectively. Measured at this repo's median of 10.64 tokens/line,
that is ≈376 lines [M].

**Design:**

- **Ratchet, not hard cap.** No file may cross the threshold. A file already over may only shrink.
- **First touch brings it under** [D]. The first change to a frozen file after go-live must bring
  that file under the cap, not merely reduce it.
- **Per-file waiver** [D]. A module that is genuinely cohesive may exceed the cap deliberately,
  carrying a one-line reason in the file. Waivers are themselves ratcheted — the count may not
  grow silently — following the pattern already used for the vulture ignore list.
- **Scope: all `.py`** [D] — `src/`, `tests/`, `.scripts/`, `.basicly/core/`.

**Measured cost at go-live** [M]:

```text
src/basicly     50 files    409,323 tok   23 over cap   worst 53,095  (13x)
tests           95 files    596,347 tok   42 over cap   worst 60,235  (15x)
.scripts         8 files     32,009 tok    3 over cap
.basicly/core   26 files    100,916 tok   10 over cap
TOTAL          179 files  1,138,595 tok   78 frozen
```

Note tests are the larger half and hold the worst offender; they will **not** fall out of a `src`
refactor as a side effect.

**Justification — and what it must never claim.** This is an **agent working-set gate**. The
support is that LLM resolve rates degrade with context length even under perfect retrieval, and
that successful SWE-bench trajectories cluster well under the threshold [S]. Stated honestly: no
study isolates *file size* against edit success — that is plausible mechanism, not measurement.

The defect-density literature must **not** be cited in support, because it argues the other way:

- Hatton 1997 (IEEE Software 14(2)) found a U-shaped curve with **mid-size components best** and
  *very small* ones worse, faults migrating to the interfaces [S].
- Koru et al. (Empirical Software Engineering 2008/2010) found a monotonic power law where
  **smaller modules are proportionally more defect-prone**, with no upturn [S].
- Ousterhout's "classitis": many shallow modules accumulate interface complexity; depth beats
  smallness [S].
- No style guide prescribes a file length. Tool defaults span 300–2000 — a 6.7× spread, which is
  itself evidence nobody measured anything [S].

That our 4,000-token threshold lands inside Hatton's 200–400 LOC minimum is **coincidence**. It
must not be presented as corroboration.

### 9.4 Test file naming

One test file per source module, enforced by name:

- `test_<module>.py` — the default.
- `test_<module>_<aspect>.py` — when one module's tests justify a split.

Measured today [M]: 48 source modules, 84 test files — 41 exact matches, 43 following the derived
form, and every source module without an exact match is covered under a derived name. The
convention is already emergent; the gate makes it binding.

---

## 10. Specification conformance

| Spec | Verdict | Action |
| --- | --- | --- |
| **AGENTS.md** — MIT | **PASS** [M]. No fields, no schema, no validator; it is plain Markdown by design | None. Do not invent structure to conform to a spec that has none |
| **Agent Skills** — Apache-2.0 / CC-BY-4.0 | **3 failures** [M]: `tool-bat`, `tool-fzf`, `tool-git-delta` on the Claude root have no `description`, a required field | The `.agents` root already synthesises one for the same skills — apply the existing behaviour to both roots |
| **Agent Plugins 1.0.0** — CC-BY-4.0 / Apache-2.0 | Total gap by absence: no `plugin.json`, no `skills/` at plugin root | Emit a conforming plugin package |

### 10.1 The `metadata` field

A map of string keys to string values; **no size cap, no reserved keys, arbitrary custom keys
explicitly permitted**; one recommendation — keep key names unique [S]. `skills.py:106-148`
already validates and round-trips it [M], and **no skill uses it**. Free capacity for loop
automation:

```yaml
metadata:
  basicly-loop-state: verify
  basicly-integrity: L3
  basicly-persona: vera
  basicly-contract: contracts/verification-evidence.md
  basicly-source-version: "0.9.0"
```

**Abuse to avoid**: non-string values; instructions the agent must obey (hosts may never show
metadata to the model, so semantics placed there vanish silently); duplicating defined fields;
large serialized blobs.

### 10.2 Do not build these

`FUTURE_CONSIDERATIONS` lists seven items the plugin spec is considering but has **not**
standardised: permissions/approval, provenance/signing, secrets, enterprise controls, **audit
trail**, dependencies, **testing/validation** [S]. Our integrity levels and gate evidence overlap
four. Encode them in skill `metadata` or a plugin `extensions` namespace — never as invented
top-level manifest fields — so migration is a rename rather than a redesign.

---

## 11. Capabilities the state of the art has and we lack

From MAST's failure taxonomy and Anthropic's published engineering, ranked by documented failure
mass [S]:

1. **Acceptance-criteria-derived verification.** MAST attributes **23.5%** of failures to
   verification itself — 8.2% none/incomplete, **9.1% incorrect**; adding objective-level
   verification gained **+15.6 points**. A generic gate suite verifies the repo, not the claim.
2. **Termination-condition and step-repetition detectors.** MAST's two largest single modes:
   step repetition 15.7%, unawareness of termination conditions 12.4%. Both are trace-level
   detectors an engine with a dispatch ledger can implement cheaply.
3. **A stated recursion policy for lane agents.** Claude Code permits nesting to depth 3 and
   withholds the Agent tool at the leaf so delegation bottoms out in work. A lane on a host that
   permits nesting can fan out inside itself, unmetered and outside the supervisor's model.
4. **Fresh-context adversarial review** distinct from gate-running — with the documented
   counter-warning adopted verbatim: a reviewer prompted to find issues **will** manufacture them.
   Bound it by severity and require findings to name a failing input.
5. **Drift detection** between tracker artifacts and code — the dominant reported failure of
   spec-driven approaches, and this repo has two recorded incidents of release claims
   contradicting code.
6. **An eval harness over our own catalog.** 8 of 34 skills have ever been exercised [M]. Dead
   definitions are unfalsifiable claims in our own catalogue.
7. **Evidence binding at SHIP** as a separate pass — the writer of a claim is the wrong context
   to audit it.

---

## 12. Observability and the two factory modes [D6]

**Measured** [M]: the harness already defaults claude dispatch to `--output-format stream-json
--verbose` (`runner.py:278`) and reads it line by line (`runner.py:1180`). It spends the stream
entirely on token accounting. `--forward-subagent-text` — verified present on Claude Code
2.1.224 — is **passed nowhere**.

| | Dark factory (`claude -p`) | Light factory (one session, built-in subagents) |
| --- | --- | --- |
| Task state | per subprocess, isolated | shared across subagents and parent |
| Progress | stream exists, unused | live, each event linked to its spawner |
| Permissions | pre-approved | prompts reach the human |
| Context | one window per lane | **one window shared by everything** |

The last row is why light cannot replace dark: many lanes cannot share one context window. Light
is for few lanes with a human present. INTAKE is inherently light — by definition it cannot run
without a human unless a requirements document is supplied.

Three items, cheapest first, none requiring headless to be abandoned: surface the stream already
read; pass `--forward-subagent-text`; add light mode as a second dispatch path.

---

## 13. Open questions

| # | Question | Blocks |
| --- | --- | --- |
| **OQ-1** | What notation for testable acceptance criteria? EARS is the candidate; nothing in the repo uses it | plan gate, verify derivation |
| **OQ-2** | How are checks *derived* from acceptance criteria — generated tests, or a judged check with a deterministic shell? | §11 item 1 |
| **OQ-3** | Validation passes, verification fails — what happens? NASA's nominal flow gives validation a *verified* product; parallel execution means validating a build that verify then rejects | D1 |
| **OQ-4** | Who assigns the integrity level — Juno, a deterministic rule over touched paths, or the requester? | D2 |
| **OQ-5** | Where do handoff artifacts live? `[policy.evidence]` exists but is presence-only | D4 |
| ~~OQ-6~~ | ~~File-size threshold~~ — **resolved**: 4,000 tokens, `SCOPE_FILE_READ_CAP` (§9.3) | — |
| ~~OQ-7~~ | ~~Exemption list or deadline~~ — **resolved**: ratchet, first touch brings the file under cap, per-file waiver with a recorded reason (§9.3) | — |
| **OQ-11** | Does the waiver need approval, or is a recorded reason enough? At L3 blast radius a self-granted waiver on a consumer surface is the weakest link in §9.3 | §9.3 |
| **OQ-12** | What is a "touch" for the first-touch rule — any diff to the file, or a non-trivial one? A one-line typo fix triggering a 13× refactor is the failure mode to avoid | §9.3 |
| **OQ-8** | Does Kill require human approval at every integrity level, or only L3? | D3 |
| **OQ-9** | House direction on PEP 758 paren-free `except A, B:` | `python-guidelines` |
| **OQ-10** | Does the plugin package ship the catalog, or is it a second distribution channel alongside `basicly install`? | §10 |

---

## 14. Sources

Institutional and primary, fetched directly: Kanban Guide (kanbanguides.org), Kanban University,
lean.org lexicon (jidoka, andon, error-proofing, heijunka, continuous-flow, pull-production,
genchi-genbutsu), Scrum Guide 2020, NIST/SEMATECH e-Handbook, Toyota UK, deming.org, PEP 8/20,
docs.python.org What's New 3.12–3.14, Astral ruff rules index, pyright configuration,
agents.md, agentskills.io/specification, agent-plugins-spec 1.0.0 and FUTURE_CONSIDERATIONS,
Anthropic engineering (multi-agent research system, building effective agents), Claude Code docs.

Paywalled, corroborated via named secondary sources: ISO/IEC/IEEE 12207:2017, 15288:2023,
29148:2018, IEEE 1012-2024, ISO 13053, ASQ (DMAIC, FMEA, fishbone, Pareto — all returned 403).

Academic: MAST failure taxonomy (arXiv 2503.13657, CC BY-NC-ND — **cite, do not reuse**);
Card, *BMJ Quality & Safety* 2017 on the limits of iterated-why; Landman et al. 2016 and
El Emam et al. 2001 on complexity metrics versus size.

Books without institutional URLs: Ohno *Toyota Production System*; Shingo *Zero Quality Control*;
Goldratt *The Goal*; Reinertsen *Principles of Product Development Flow*; Radice et al., ETVX,
IBM Systems Journal 24(2), 1985; Cooper, Stage-Gate, *Business Horizons* 33(3), 1990.

**Licence flags**: `anthropics/skills` is per-folder, and its document skills are
source-available, **not** open source. `awesome-claude-code` and the MAST paper are CC BY-NC-ND —
no commercial reuse, no derivatives.

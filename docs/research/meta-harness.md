# Meta harness: state, lessons from T3 Code, and the self-improving loop

Record: basicly-upnhikm. All counts were measured at 2026-11-19T20:43Z unless a row says otherwise.

In the ledger, "meta harness" means basicly-qpr2xmr: one interface that drives Claude Code, Codex and Copilot on the owner's own subscriptions.
The Orchestrator decided on 2026-11-19 that the owner's word "metaharness" follows the ledger.
Section A covers basicly-qpr2xmr.
Section B covers the loop by which the harness improves itself (basicly-1dpggot) and how that loop feeds Section A.
Section C lists the deepseek concepts that are placed but not built.

## Result

- Three records in progress are past the 2-day limit of the Agentic Kanban rules: basicly-qpr2xmr.3.4, basicly-qpr2xmr.3.10 and basicly-qpr2xmr.3.13. Each must go back to the queue or close with a reason today.
- 13 children of basicly-qpr2xmr.3 are in progress at the same time, all held by one person. The limit is 2 per agent. Finish them before any new record in this plan starts. The Orchestrator decides which 2 stay in progress.
- The acceptance of the meta harness is requirement R11: daily use over 14 days, counted from run records. No record before stage basicly-qpr2xmr.4.2 lets the owner run a real session through the harness. So the R11 count cannot start before that stage. The cuts below lead to it by the shortest path.
- The self-improving loop has every command it needs except a verified close. The lessons log holds 27 lessons and none is closed. No export has become a landed catalog change.
- T3 Code stays a reference (decision D2). See `docs/research/t3-code.md`.

## Section A: the meta harness, basicly-qpr2xmr

### State

Measured with `basicly tracker show <id> --view current`, counting parent-child edges only.

| Level | Records | Open | In progress | Closed |
| --- | --- | --- | --- | --- |
| Children of the epic | 6 | 4 | 1 | 1 |
| Children of basicly-qpr2xmr.3 | 30 | 8 | 13 | 9 |
| Children of basicly-qpr2xmr.4 | 6 | 6 | 0 | 0 |
| Total | 42 | 18 | 14 | 10 |

- The children of the epic include basicly-qpr2xmr.6, which this review filed.
- The current cut is the orchestrator kit under basicly-qpr2xmr.3 (owner triage 2026-11-17, shape agreed on 2026-11-18).
- basicly-qpr2xmr.3.4, basicly-qpr2xmr.3.10 and basicly-qpr2xmr.3.13 were last changed on 2026-11-17 between 20:02Z and 20:22Z.
- basicly-qpr2xmr.1 (unpaid probes) and basicly-qpr2xmr.2 (written vendor permission) have not changed since 2026-11-04.
- Stages basicly-qpr2xmr.4.2 to basicly-qpr2xmr.4.6 move to the tracker of the new meta harness project when basicly-qpr2xmr.4.1 creates it (comment on the epic, 2026-11-04).

### Owner decisions that set the order

The epic records these owner decisions of 2026-11-04. The cut order follows them.

- v1 is a single-engine session, Claude first. This answers open question Q3 of requirements v4.
- Codex is blocked until OpenAI text permits it. This answers Q4 for now.
- Personal use, with the request rate capped at direct use, and Anthropic asked in writing. Q1 waits for that answer, which basicly-qpr2xmr.2 records.

No new owner decision is needed for the order below.

### What T3 Code teaches it

`docs/research/t3-code.md` holds the evidence.

- T3 Code fails the terminal-first goal and requirements R5 and R8, so decision D2 stays.
- Three patterns go into requirement R1 through basicly-qpr2xmr.6: capability flags, replay fixtures recorded from the real vendor client, and a permission ceiling that refuses a turn by name.
- T3 Code runs Claude through the Claude Agent SDK. Whether the SDK may run on a subscription login is part of Q1.

### Next cuts in delivery order

| Order | Record | Done when |
| --- | --- | --- |
| 1 | the 13 children in progress under basicly-qpr2xmr.3 | at most 2 are in progress per holder |
| 2 | basicly-qpr2xmr.6 | a requirements v5 comment holds the three T3 Code patterns |
| 3 | basicly-qpr2xmr.3.1, .3.2, .3.3 | the note class, the private vendor home and store format version 2 are landed |
| 4 | basicly-qpr2xmr.4.1 | the meta harness repository exists as a basicly consumer |
| 5 | basicly-qpr2xmr.4.2 | the owner runs one Claude session through it, and the R11 count starts |

- **Order 1.** The check is a count of in-progress children of basicly-qpr2xmr.3 per holder from `basicly tracker show`.
- **Order 5.** This is the first point at which R11 can be measured. It needs the Claude path cleared under R0.
- **Owner-blocked.** basicly-qpr2xmr.2 waits for vendor text. basicly-qpr2xmr.1 needs probes that the owner approves. Neither is a lane. Both block order 5 for the paths they cover.
- **Not in this order.** basicly-qpr2xmr.3.7, .3.8, .3.12 and .3.14 (probes and herdr hooks) and stages basicly-qpr2xmr.4.3 to basicly-qpr2xmr.4.6 come after order 5. v1 is one engine, so they do not block it.

## Section B: the self-improving loop, basicly-1dpggot

The owner's intent (2026-11-17, relayed by the Orchestrator): "basicly must be able to learn and self-improve - if it is happening in a client repo, then it must know how to bring back the learning to the main basilcy repo, for example by creating a PR."

### The loop

A learning passes five steps.

| Step | Command | State |
| --- | --- | --- |
| 1 Detect a failure | `.scripts/orch_errors.py`, the retrospector | built |
| 2 Record a lesson | `orch lesson add` | built, in use |
| 3 Turn lessons into a patch | `orch lessons export` | built, never used |
| 4 Land the patch | normal catalog authoring | never reached |
| 5 Close the lesson | `orch lesson close` | built, never used |

- **Step 1.** `.scripts/orch_errors.py` counts tool and script errors by class in Claude Code transcripts only (`main`, `.scripts/orch_errors.py:272`). Record basicly-qpr2xmr.3.19 says it moves into the kit after cut 4, and cut 4 has landed. The retrospector fires on a special-cause signal in the gate-failure ledger. Only `loop.py` calls it (`_retrospective`, `src/basicly/loop.py:1550`). Record basicly-gtb19bg.30.2 names the headless loop as a removal candidate.
- **Step 2.** The workspace lessons log holds 27 lessons from 2026-11-18 and 2026-11-19, from the members dev, orchestrator and Orchestrator. Measured with `jq` over the log.
- **Step 3.** `orch lessons export --target FILE --out FILE` appends each open lesson to one target file and prints a diff. It is not a patch against catalog sources. It refuses a target outside the current repository (`target.relative_to(repo)` in `.basicly/core/kit/orch/orch_lessons/cli.py`).
- **Step 5.** `orch lesson close` accepts any text as the change. It does not check that a commit exists. `orch doctor` reports only lessons open longer than 14 days.
- **No close.** All 27 lessons are `add` events and none has a `close` event. `git log --grep lesson` on main finds the export feature commit ab1ead7b and no commit that came from an export.
- **Outside the loop.** Each agent also keeps a per-user memory. For this repository it holds 160 entries (`ls` of the memory folder). That memory never returns to the repository.
- **Consumer to basicly.** No command takes a lesson from a consumer repository back to basicly. This is the missing part of the owner's intent.

### How Section B feeds Section A

- The meta harness drives three vendors. Step 1 reads only Claude Code transcripts. Each meta harness adapter will need a transcript reader. That work belongs to the adapter stages, not to this plan.
- The lessons log records the member, not the vendor. Lessons from all three vendors can go into one log.
- A landed lesson changes catalog sources. `basicly build` projects them into `CLAUDE.md`, `AGENTS.md` and the Copilot instructions. One lesson improves all three engines.
- The meta harness will be a basicly consumer (basicly-qpr2xmr.4.1). Cut L3 is the path by which its lessons return to basicly.

### Cut plan

Each cut is one lane under basicly-1dpggot.

| Cut | Change | Acceptance test |
| --- | --- | --- |
| L0 | Shape basicly-1dpggot with the Orchestrator | the record states how a consumer recognises a learning, what evidence it carries, and its acceptance criteria |
| L1 | `orch lesson close` refuses a change that is not a commit on the main branch | a test closes with an unknown commit and gets a refusal that names it, and closes with a real commit and gets a `close` event |
| L2 | Move `.scripts/orch_errors.py` and `.scripts/orch_error_classes.py` into the kit as `orch errors` | `orch errors --help` exits 0 from the kit, and `tests/test_orch_errors.py` passes against the kit path |
| L3 | `orch lessons export` takes a basicly checkout as the target and maps each lesson to the catalog source it changes | a test in a consumer fixture writes a patch that names only basicly catalog source paths, with the network blocked, and refuses a target that is not a basicly checkout by name |
| L4 | Write a pull request body from the export and print the `gh pr create` command without running it | a test checks the printed command and that no process starts |

- L0 comes first, because L3 depends on how a learning is recognised.
- After L1 lands, close the loop once by hand: export, land the change, close the lessons it answers. This is operating work for the Orchestrator, not a lane.
- L4 prints the command and does not run it, because a pull request is an outward action that needs a person's approval.
- The retrospector: if basicly-gtb19bg.30.2 removes the headless loop, the special-cause detector must move first. This is a condition on that record, not a cut here.

## Section C: deepseek concepts placed but not built

Source: `docs/research/deepseek-harness-plugin-review.md`, "Dispositions". Only concepts marked adopt or adapt that are not built are listed. State measured with `basicly tracker show <id> --view current`.

| Concept | Record | State |
| --- | --- | --- |
| A2, A6 profiles and requirements | basicly-gtb19bg.29.2 | open, P2 |
| A3, A19 hook dispatch mode, fail closed | basicly-gtb19bg.29.6 | open, P2 |
| A5 disposer per registration | basicly-gtb19bg.29.3 | open, P2 |
| A7 patch a row by id | basicly-gtb19bg.29.2 | partly built |
| A8 dump the composed tree | basicly-gtb19bg.29.2 | partly built |
| A13, B8 required on read | basicly-3ghjmmg | open, no priority |
| A15, B5 seam completeness | basicly-gtb19bg.29.5 | open, P2 |
| A17 where a capability goes | basicly-gtb19bg.29 | no child record |
| A18 generated capability map | basicly-4yz1mh0 | open, P3 |
| A20 fresh child per round | basicly-oo60vym | open, P3 |
| B1, B17 dispatch outcome vocabulary | basicly-qpr2xmr | no child record |
| B2 argv substitution | basicly-gtb19bg.29.7 | open, P2 |
| B3 refuse an option by name | basicly-qpr2xmr.6 | open, P2 |
| B4 minimal profile as a floor | basicly-gtb19bg.2 | in progress, P1 |
| B10 limitations field | basicly-j52rncv | open, P3 |
| B11 run a document's commands | basicly-n57j7ex | open, P3 |
| B12 positive control as a gate | basicly-rj1bbse | open, P3 |
| B18 durable inbox | basicly-qpr2xmr.3 | in progress |
| B22 advisory scopes declared | basicly-omtfx6t | open, no priority |

- **A7.** A `.basicly-local` root overrides core in `basicly catalog dump`.
- **A8.** `basicly catalog dump` lists fragments only. On main its output matches "fragment" on 67 lines and "skill" or "hook" on none.
- **A14, B20.** These are built, so they are not listed.
- **B3.** The review placed B3 on the epic. basicly-qpr2xmr.6 now carries it as "a missing operation is refused by name".
- **No child record.** A17, B1 and B17 point at an epic, and no child record carries them.
- **No priority.** basicly-3ghjmmg and basicly-omtfx6t carry no priority, so the ready ranking cannot place them.

## Not established

- Whether the 13 children in progress under basicly-qpr2xmr.3 are all active work. The holder field shows one name for all of them.
- Whether a lesson maps to exactly one catalog source. L3 must find this out.
- Whether basicly-gtb19bg.30.2 will remove the headless loop. The record names it as a candidate only.

# Meta harness: state, lessons from T3 Code, and the self-improving loop

Record: basicly-upnhikm. All counts were measured at 2026-11-19T20:43Z unless a row says otherwise.

In the ledger, "meta harness" means basicly-qpr2xmr: one interface that drives Claude Code, Codex and Copilot on the owner's own subscriptions.
The Orchestrator decided on 2026-11-19 that the owner's word "metaharness" follows the ledger.
Section A covers basicly-qpr2xmr.
Section B covers the loop by which the harness improves itself (basicly-1dpggot) and how that loop feeds Section A.
Section C lists the deepseek concepts that are placed but not built.

## Result

- Update 2026-11-26 (basicly-e44r45l): the first running piece is `orch delegate` in basicly-orch. It hands one turn to Claude Code, Codex or Copilot in a lane and writes the answer on the record; see `packages/basicly-orch/README.md`, section "Coding agents". The rest of this note is the state of 2026-11-19.

- The acceptance of the meta harness is requirement R11: daily use over 14 days, counted from run records. Stage basicly-qpr2xmr.4.2 is the first record that lets the owner run a real session through the harness. It is blocked by basicly-qpr2xmr.4.1 and basicly-qpr2xmr.1, and basicly-qpr2xmr.4.1 is blocked by all of basicly-qpr2xmr.3. So every open child of basicly-qpr2xmr.3 stands between the owner and R11.
- 13 children of basicly-qpr2xmr.3 are in progress at the same time. The holder field shows one name for all of them, because every session writes the same holder. So the field cannot count work in progress per agent, and the Agentic Kanban limit of 2 per agent cannot be checked from the ledger.
- Three of those records had no change for more than 2 days at the time of measurement: basicly-qpr2xmr.3.4, basicly-qpr2xmr.3.10 and basicly-qpr2xmr.3.13.
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
- basicly-qpr2xmr.1 (unpaid probes) and basicly-qpr2xmr.2 (written vendor permission) have not changed since 2026-11-04. The owner approved the unpaid probes on 2026-11-04, so basicly-qpr2xmr.1 can start.
- Dependencies, from `basicly tracker show`: basicly-qpr2xmr.4.1 is blocked by basicly-qpr2xmr.3, and basicly-qpr2xmr.4.2 is blocked by basicly-qpr2xmr.4.1 and basicly-qpr2xmr.1.
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
- Three patterns go into the requirements through basicly-qpr2xmr.6 (R1, and R2 or R7 for the ceiling): capability flags, replay fixtures recorded from the real vendor client, and a permission ceiling that refuses a turn by name.
- T3 Code runs Claude through the Claude Agent SDK. Whether the SDK may run on a subscription login is part of Q1.

### Next cuts in delivery order

| Order | Record | Done when |
| --- | --- | --- |
| 1 | the 13 children in progress under basicly-qpr2xmr.3 | each is landed, or back in the queue with a reason |
| 2 | basicly-qpr2xmr.6 | a requirements v5 comment holds the three T3 Code patterns |
| 3 | the 8 open children of basicly-qpr2xmr.3 | each is landed, or closed with a reason |
| 4 | basicly-qpr2xmr.1 | each unpaid probe has a recorded result |
| 5 | basicly-qpr2xmr.4.1 | the meta harness repository exists as a basicly consumer |
| 6 | basicly-qpr2xmr.4.2 | the owner runs one Claude session through it, and the R11 count starts |

- **Order 3.** basicly-qpr2xmr.3.12 needs a booked session with the owner. basicly-qpr2xmr.3.14 needs the owner to approve a user settings diff.
- **Order 4.** It can run beside orders 1 to 3, because it does not touch basicly-qpr2xmr.3.
- **Order 6.** This is the first point at which R11 can be measured. The Claude path must have its vendor citation under R0 first.
- **Owner-blocked.** basicly-qpr2xmr.2 waits for vendor text. It is not a lane. Requirement R0 makes it block order 6 for the Claude path. The ledger has no edge for this, because basicly-qpr2xmr.2 blocks only basicly-qpr2xmr.4.4.
- **After order 6.** Stages basicly-qpr2xmr.4.3 to basicly-qpr2xmr.4.6 add engines and views. v1 is one engine, so they do not block R11.

## Section B: the self-improving loop, basicly-1dpggot

The owner's intent (2026-11-17, relayed by the Orchestrator): "basicly must be able to learn and self-improve - if it is happening in a client repo, then it must know how to bring back the learning to the main basilcy repo, for example by creating a PR."

### What exists

| Part | Where | Count |
| --- | --- | --- |
| Skills | `.basicly/core/skills` | 31 |
| Hook files | `.basicly/core/hooks` | 41 |
| Agent definitions | `.basicly/core/agents` | 13 |
| Decision by vote | the `council` skill | 1 |
| Review by lenses | the `swarm` and `hill-climb` skills | 2 |
| Orchestrator kit | `.basicly/core/kit/orch` | 4 packages |

Counted on main: skill folders with `ls -d`, tracked hook files with `git ls-files`, and `agent.yaml` files at the measurement time. The councils and swarms decide and review. They do not write lessons, so their findings reach the loop only when a session adds a lesson by hand.

### The loop

A learning passes five steps.

| Step | Command | State |
| --- | --- | --- |
| 1 Detect a failure | `.scripts/orch_errors.py`, the retrospector | built |
| 2 Record a lesson | `orch lesson add` | built, in use |
| 3 Turn lessons into a patch | `orch lessons export` | built, never used |
| 4 Land the patch | normal catalog authoring | never reached |
| 5 Close the lesson | `orch lesson close` | built, never used |

- **Step 1.** `.scripts/orch_errors.py` counts tool and script errors by class in Claude Code transcripts only (`main`, `.scripts/orch_errors.py:297`). Record basicly-qpr2xmr.3.19 says it moves into the kit after cut 4, and cut 4 has landed. The retrospector fires on a special-cause signal in the gate-failure ledger. Only `loop.py` calls it (`loop._retrospective`). Record basicly-gtb19bg.30.2 names the headless loop as a removal candidate.
- **Step 2.** The workspace lessons log holds 27 lessons from 2026-11-18 and 2026-11-19, from the members dev, orchestrator and Orchestrator. Measured with `jq` over the log.
- **Step 3.** `orch lessons export --target FILE --out FILE` appends each open lesson to one target file and prints a diff. It is not a patch against catalog sources. It refuses a target outside the current repository (`target.relative_to(repo)` in `.basicly/core/kit/orch/orch_lessons/cli.py`).
- **Step 5.** `orch lesson close` accepts any text as the change. It does not check that a commit exists. `orch doctor` reports only lessons open longer than 14 days.
- **No close.** All 27 lessons are `add` events and none has a `close` event. `git log --grep lesson` on main finds the export feature commit ab1ead7b and no commit that came from an export.
- **Outside the loop.** Each agent also keeps a per-user memory. For this repository it holds 157 memory files, not counting the index files. That memory never returns to the repository.
- **Consumer to basicly.** No command takes a lesson from a consumer repository back to basicly. This is the missing part of the owner's intent.

### How Section B feeds Section A

- The meta harness drives three vendors. Step 1 reads only Claude Code transcripts. Each meta harness adapter will need a transcript reader. That work belongs to the adapter stages, not to this plan.
- The lessons log records the member, not the vendor. Lessons from all three vendors can go into one log.
- A landed lesson changes catalog sources. `basicly build` projects them into `CLAUDE.md`, `AGENTS.md` and the Copilot instructions. One lesson improves all three engines.
- The meta harness will be a basicly consumer (basicly-qpr2xmr.4.1). Cut L3 is the path by which its lessons return to basicly.

### Cut plan

Each cut is one lane. Cuts L0 to L4 are filed as basicly-1dpggot.1 to basicly-1dpggot.5, in that order. L3 is blocked by L0, and L4 by L3.

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
| B18 durable inbox | basicly-qpr2xmr.3 | not established |
| B22 advisory scopes declared | basicly-omtfx6t | open, no priority |

- **A7.** A `.basicly-local` root overrides core in `basicly catalog dump`.
- **A8.** `basicly catalog dump` lists fragments only. On main its output matches "fragment" on 67 lines and "skill" or "hook" on none.
- **A14, B20.** These are built, so they are not listed.
- **B3.** The review placed B3 on the epic. basicly-qpr2xmr.6 now carries it as "a missing operation is refused by name".
- **No child record.** A17, B1 and B17 point at an epic, and no child record carries them.
- **B18.** `orch send` exists. Whether its store keeps one file per message, as design section 8 says, was not checked.
- **No priority.** basicly-3ghjmmg and basicly-omtfx6t carry no priority, so the ready ranking cannot place them.

## Not established

- Which session works each of the 13 children in progress under basicly-qpr2xmr.3. The holder field shows one name for all of them.
- Whether a lesson maps to exactly one catalog source. L3 must find this out.
- Whether basicly-gtb19bg.30.2 will remove the headless loop. The record names it as a candidate only.

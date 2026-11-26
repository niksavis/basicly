# deepseek-harness plugin model: what basicly adopts

Record: basicly-gtb19bg.28. Read on 2026-11-18.

## Result

basicly adopts the ideas of the deepseek-harness plugin model that fit a repository-owned catalog. It refuses the ideas that need an in-process session runtime.

- **Adopt (6):** dump the composed config, projection as a fold over events, guards that fail closed, refuse an unsupported option by name, a minimal profile as a measurement floor, and tombstoned deletes with one holder per record.
- **Adapt (20):** most of them land in the plugin composition work, basicly-gtb19bg.29, in the orchestrator kit, and in five new records.
- **Refuse (18):** each one is recorded with its reason. Two of them are refused because deepseek-harness has withdrawn the idea itself.

basicly takes no code. deepseek-harness is a session runtime, and basicly is a catalog, a set of gates and a tracker.

## Sources

- **deepseek-harness:** tag `dsh-v0.2.2-rc.5`, published 2026-11-18, commit 9c2670d3. Base URL: <https://github.com/deepseek-ai/deepseek-harness/blob/dsh-v0.2.2-rc.5/>
- **Files read at that tag:** README.md, LICENSE (stock MIT, no rider), AGENTS.md, docs/architecture.md, docs/cordis-primer.md, docs/capability-seams.md, docs/glossary.md, docs/tool-execution-pipeline.md, docs/event-producer-consumer.md, docs/agent-lifecycle.md, docs/session-format-status.md, docs/rescope.md, docs/defensive-patterns/README.md, and docs/upgrade-guide/v0.2.0-rc.2/remove-runtime-invariants/guide.md.
- **The August research:** `git show 78962968:docs/research/2026-08-17-deepseek-harness.md` (1479 lines). Its decision D-43 is in `docs/architecture/architecture.md` at about line 5339.
- **The 22 undisposed concepts:** items 1 to 22 of the closed record basicly-hnnmk9.8, in its section "The UNADDRESSED analysis". Its section "Which core each speaks to" names 16 of them as harness concepts.
- **basicly:** 0.46.3, main at 3f3122c9.

The deepseek-harness citations below are relative to the base URL. "Old" means a line of the August research.

## Changes since the last review

1. **deepseek-harness withdrew two August concepts.** It removed the runtime-invariant plane after v0.2.0-rc.2 (remove-runtime-invariants/guide.md:12-14). Its goal fold now checks on replay, not before an append (packages/goal/goal/README.md:100 and :161). So "pre-append validation by an independent fold" no longer has an example to copy.
2. **The basicly evidence for pre-append validation is gone.** The frozen fsck allowance it cited is empty (`pyproject.toml:313`). The tracker fold counts unknown event kinds (`_folded`, `.basicly/core/kit/tracker/events.py:747-755`), and fsck reports them as a warning (`_unfolded_kind_findings`, `fsck.py:385-404`).
3. **The stopReason idea has no surviving basicly caller.** Every caller of `provider_limit` is in `src/basicly/supervise.py`, which basicly-gtb19bg.30.2 removes.
4. **Stale citations.** basicly-hnnmk9.4 and basicly-hnnmk9.8 cite the first lines of `provider_limit.py` and lines 78 to 80 of `events.py`. Commit 4f6922db stripped the prose at both places. The records also cite D-43 at the old lines 4833-4882 and deepseek-harness at rc.1.

## Dispositions

### Current deepseek-harness concepts

| # | Concept | Source | Disposition | Reason | Record |
| --- | --- | --- | --- | --- | --- |
| A1 | Everything is a plugin; there is no privileged core | architecture.md:9-11 | refuse | D-43 P4 refused it, and nothing at rc.5 changes that | basicly-gtb19bg.28 |
| A2 | Declared provides and requires; load order follows from them | cordis-primer.md:10-11 | adapt | A profile install refuses a plugin whose requirement is absent | basicly-gtb19bg.29.2 |
| A3 | A declared dispatch mode for each event | cordis-primer.md:12,17-27 | adapt | Applied to agent hook events only, because basicly runs no in-process events | basicly-gtb19bg.29.6 |
| A4 | Waterfall middleware | cordis-primer.md:29-35 | refuse | Needs an in-process runtime, which D-43 P2 refuses | basicly-gtb19bg.28 |
| A5 | Every registration has a disposer | cordis-primer.md:13,45 | adapt | Uninstall and disable remove what was projected; user effects are not undone | basicly-gtb19bg.29.3 |
| A6 | A profile stacks bundles as ordered layers | architecture.md:15-23 | adapt | A static selection of catalog data, so an install carries only the capabilities it uses | basicly-gtb19bg.29.2 |
| A7 | A patch replaces a row by id | architecture.md:25 | adapt | Partly built: a `.basicly-local` root overrides core in `catalog dump` | basicly-gtb19bg.29.2 |
| A8 | `--dump-config` prints the composed tree | architecture.md:31-37 | adopt | `basicly catalog dump` (`cmd_catalog`, `src/basicly/cli.py:3193`) covers fragments; skills and hooks are still missing | basicly-gtb19bg.29.2 |
| A9 | One launcher, with a gate against bypasses | architecture.md:43-45 | refuse | The tracker kit must work without the engine, so it is a deliberate second entry | basicly-gtb19bg.28 |
| A10 | Three event domains and an authoring rule for them | architecture.md:69-73 | refuse | deepseek-harness dropped the rule at rc.5; rule placement lives in catalog-authoring | basicly-gtb19bg.28 |
| A11 | Turn, step and round loop | architecture.md:83-114 | refuse | basicly is not a session runtime, and the headless loop is being removed | basicly-gtb19bg.30.2 |
| A12 | What the model sees is logged | architecture.md:130 | refuse | Already disposed: a fact that is not written down does not survive the session | basicly-gtb19bg.28 |
| A13 | An event is required on read unless marked ignorable | architecture.md:128; session-format-status.md:20-24 | adapt | Tracker design v8 proposes a fold flag on holder and status kinds, so an old tool refuses them | basicly-3ghjmmg |
| A14 | Projection as registered folds over events | architecture.md:132 | adopt | Already built: the tracker state is a fold over the event log (`fold`, `.basicly/core/kit/tracker/events.py:700`) | basicly-gtb19bg.28 |
| A15 | A seam is a definition, a provider and a consumer | architecture.md:138 | adapt | A completeness check on the plugin map | basicly-gtb19bg.29.5 |
| A16 | Agent scope with shadowing and restriction | glossary.md:13-21 | refuse | basicly filters per target instead, through `applies_to` | basicly-gtb19bg.28 |
| A17 | A table of where new behaviour goes | architecture.md:144-169 | adapt | A map of where a new plugin capability goes | basicly-gtb19bg.29 |
| A18 | Catalogs generated from code, with a drift gate | capability-seams.md:1-6; event-producer-consumer.md:1-11 | adapt | Generate the plugin capability map instead of keeping a one-off scan | basicly-4yz1mh0 |
| A19 | Tool guards fail closed; a missing approval denies | tool-execution-pipeline.md | adopt | Matches the mistake-proofing rule; hook events state deny-on-absence | basicly-gtb19bg.29.6 |
| A20 | A fresh child per round, with a bounded handoff | glossary.md:44-46 | adapt | Matches the owner's rule that subagents are for fresh-context work | basicly-oo60vym |
| A21 | Defensive review patterns | defensive-patterns/README.md | refuse | The same rules already ship as fragments | basicly-gtb19bg.28 |
| A22 | Claude Code and Codex hook adapters as plugins | event-producer-consumer.md:25 | refuse | Unknown whether they read basicly's projected hook files; no consumer has asked | basicly-gtb19bg.28 |

### The 16 harness concepts from the August research

| # | Concept | Old | Disposition | Reason | Record |
| --- | --- | --- | --- | --- | --- |
| B1 | A dispatch result that never rejects and carries `stopReason` | 430-431 | adapt | The only basicly caller is being removed; the shape belongs in the new dispatch interface | basicly-qpr2xmr |
| B2 | Fail-closed confinement that substitutes the argv | 302-307 | adapt | Depends on whether each agent's PreToolUse hook can rewrite input; unknown | basicly-gtb19bg.29.7 |
| B3 | Refuse an unsupported option by name | 1028-1032 | adopt | It is the mistake-proofing rule | basicly-qpr2xmr |
| B4 | A minimal composition as the measurement floor | 1082-1084 | adopt | A gates-only profile is the floor the payoff measurement needs | basicly-gtb19bg.2 |
| B5 | Seam three-role completeness | 295-300 | adapt | Same as A15 | basicly-gtb19bg.29.5 |
| B6 | A gate graph declared with `needs:` | 484-492 | refuse | basicly checks run in parallel with fail-fast (`ParallelChecks`, `verify_parallel.py:32`); no waste measured | basicly-gtb19bg.28 |
| B7 | Pre-append validation by an independent fold | 502-507 | refuse | deepseek-harness withdrew it, and the basicly cost evidence is gone | basicly-gtb19bg.28 |
| B8 | Required on read unless ignorable | 459-461 | adapt | Same as A13 | basicly-3ghjmmg |
| B9 | Runtime-invariant plane | 494-500 | refuse | deepseek-harness removed it after v0.2.0-rc.2 | basicly-gtb19bg.28 |
| B10 | A required "Known Limitations" section | 512-515 | adapt | A declared limitations field on each skill and fragment source | basicly-j52rncv |
| B11 | A gate that runs a command a document prints | 1404-1412 | adapt | docs-claims catches invented commands only, not failing ones | basicly-n57j7ex |
| B12 | A positive control enforced as a gate | 1219-1222 | adapt | Today a fragment rule, and the fault recurs | basicly-rj1bbse |
| B13 | "What the model sees" and cache-effect declarations | 517-520 | refuse | `token_cost` covers cost; the cache effect is unmeasured | basicly-gtb19bg.28 |
| B14 | Event-domain authoring rule | 288-291 | refuse | Same as A10 | basicly-gtb19bg.28 |
| B15 | A format-adoption date pin | 533-535 | refuse | `token_cost` already keys its window on version (`REQUIRED_FROM_VERSION`, `catalog_token_cost.py:19,44`) | basicly-gtb19bg.28 |
| B16 | No central index; the path is the metadata | 1095-1100 | refuse | The vendor skill listing is the index an agent reads | basicly-gtb19bg.28 |

### The six concepts the first pass left out

basicly-hnnmk9.8 lists 22 undisposed concepts. Its section "Which core each speaks to" names the 16 harness concepts as items 2 to 6, 9 to 16, 18, 20 and 22. The table above took items 1 to 16 instead. So items 1, 7 and 8, which speak to the execution process, are disposed, and items 18, 20 and 22 were not. The rows below dispose items 17 to 22, so all 22 now carry a disposition.

| # | Concept | Item | Disposition | Reason | Record |
| --- | --- | --- | --- | --- | --- |
| B17 | A code discriminator on the single blocked state | 17 | adapt | The tracker has one `blocked` status (`KNOWN_STATUSES`, `src/basicly/loop_state.py:13`) with no cause code. The cause belongs in the dispatch outcome vocabulary, with B1 | basicly-qpr2xmr |
| B18 | A continuable child with a durable inbox and structural follow-up authority | 18 | adapt | The orchestrator kit gives each member a durable inbox, one file per message, moved to `cur` only on read, and the tool sets the sender from the caller's bound session (`docs/design/orchestrator-kit.md`, section 8) | basicly-qpr2xmr.3 |
| B19 | A compare-and-set revision fence on a record | 19 | refuse | The ledger is an append-only event log that merges by union across offline writers (`.gitattributes` `merge=union`); a fence on one record would refuse a valid offline merge. A competing claim is reported as contested instead | basicly-gtb19bg.28 |
| B20 | Tombstoned deletes and single-writer cross-assignment on the shared board | 20 | adopt | Already built: a deleted id stays minted and is never reused (`minted_ever`, `.basicly/core/kit/tracker/ids.py:110-112`), and each record has one holder, with a competing claim reported as contested | basicly-gtb19bg.28 |
| B21 | Withdrawal visible one step early, and staleness checked at two levels | 21 | refuse | It needs an in-process plugin runtime, which D-43 P2 refuses, as for A4 | basicly-gtb19bg.28 |
| B22 | Advisory write scopes declared as advisory, with the bypasses enumerated | 22 | adapt | The plan gate checks scope globs for overlap, and `with_scope_fence` (`src/basicly/contention.py:56`) puts sibling scopes in the prompt, but nothing states that the scopes are advisory or lists the bypasses | basicly-gtb19bg.28 (a follow-up record is requested) |

## Where each kept concept lives

Each adopted or adapted concept is placed on the two axes of the orchestrator kit design (`docs/design/orchestrator-kit.md`, section 15.2): engine or interface, and inside the repository or outside it.

### Engine against UI

- **Engine** means the catalog, the gates, the tracker kit and the dispatch core, which run with no screen.
- **Interface** means a command's output, a board page or a vendor adapter that shows or carries engine data.

| # | Concept | Side | Why |
| --- | --- | --- | --- |
| A2 | Declared provides and requires | engine | install refuses by data, with no screen |
| A3 | A dispatch mode per hook event | interface | it is the vendor hook adapter's contract |
| A5 | A disposer for each registration | engine | uninstall removes what install projected |
| A6 | Profiles as ordered layers | engine | a static selection of catalog data |
| A7 | A patch replaces a row by id | engine | the overlay fold in the catalog |
| A8 | Dump the composed tree | interface | a command that prints engine state |
| A13 / B8 | Required on read unless ignorable | engine | a fold rule of the tracker kit |
| A14 | Projection as a fold | engine | the tracker state fold |
| A15 / B5 | Seam completeness | engine | a lint over the plugin map |
| A17 | Where a new capability goes | engine | a catalog authoring map |
| A18 | Generated capability map with a drift gate | engine | a gate |
| A19 | Guards fail closed | interface | vendor hook adapters answer deny |
| A20 | A fresh child per round | engine | the dispatch core |
| B1 / B17 | Dispatch outcome vocabulary | engine | the edge between two states |
| B2 | Argv substitution | interface | a vendor PreToolUse adapter |
| B3 | Refuse an unsupported option by name | engine | the dispatch brief and its options |
| B4 | A minimal profile as a floor | engine | a catalog selection |
| B10 | A declared limitations field | engine | a catalog schema field |
| B11 | A gate that runs a document's command | engine | a gate |
| B12 | A positive control as a gate | engine | a gate |
| B18 | A durable inbox | engine | the kit mail store; delivery to a pane is an adapter |
| B20 | Tombstones and one holder | engine | the tracker kit |
| B22 | Advisory scopes declared | engine | the plan gate; the prompt fence is its interface |

### Repository against outside it

- **Repository** means committed catalog sources, configuration, projected files and the ledger.
- **Outside** means state per user or per machine: the mail store, approvals, the lessons log, session files and the vault.

| # | Concept | Place | Why |
| --- | --- | --- | --- |
| A2, A5, A6, A7, A15, A17, A18, B3, B4, B10, B11, B12, B22 | catalog, gates and plan rules | repository | they are catalog data or gates that the repository commits |
| A3, A19, B2 | hook adapters | repository | the projected hook settings are committed; a person's own user settings are outside |
| A8 | Dump the composed tree | repository | it reads the repository's catalog only |
| A13 / B8, A14, B17, B20 | ledger folds and records | repository | the ledger is committed; a vault copy is outside |
| A20, B1 | dispatch runs | outside | a run's session and result live per machine; only the outcome vocabulary is committed |
| B18 | Durable inbox | outside | the mail store is per user (design section 15.2) |

## Not established

- **B2:** whether Claude Code, Codex and Copilot PreToolUse hooks can rewrite tool input. Settle it with `interface-facts` before basicly-gtb19bg.29.7 builds on it.
- **B3:** whether `DEFERRED_AGENT_OPTIONS` still exists at rc.5. Its old path is absent.
- **B6:** whether the gate runner still uses `needs:`.
- **B16:** the no-index rule at rc.5. It was not re-read.
- **A22:** which hook files the deepseek-harness adapters read.
- **Coverage:** the 138 subsystem pages changed since rc.1 were not read. The compare API lists only 300 of 1587 changed files.
- **A5:** whether `basicly install` is atomic on failure.

# Reference Register — State-of-the-Art Review, 2026-07-26

Companion to [`2026-07-26-sota-review.md`](2026-07-26-sota-review.md). This file is the
**provenance and licence record**: what was read, at which revision, under which licence, and
how much confidence each source earns. Its job is to make the review re-runnable and to make
every borrowed idea traceable to something we are actually permitted to borrow.

Two rules govern use of everything below:

1. **Concepts are free; text is not.** We adopt ideas, vocabulary, and architectural patterns.
   We do not copy prose, prompts, or code from these repos into `basicly` sources without
   satisfying §1's licence column and adding the required attribution.
2. **A licence claim is checked, not assumed.** §1 records what each `LICENSE` file actually
   says as of the pinned revision. One repo in this set is *not* what our own docs previously
   claimed it was (§2).

## 1. Cloned repositories

Cloned to `development/reference-repos/` (outside `basicly`, ignored by the workspace's
whitelist `.gitignore`). Shallow clones, `--depth 50`.

| Repo | Pinned revision | Date | Licence | Use permitted |
| --- | --- | --- | --- | --- |
| [mattpocock/skills](https://github.com/mattpocock/skills) | `ed37663` | 2026-07-21 | MIT (Matt Pocock, 2026) | Yes — concepts and, with attribution, text |
| [obra/superpowers](https://github.com/obra/superpowers) | `3dcbd5c` (v6.2.0) | 2026-07-23 | MIT (Jesse Vincent, 2025) | Yes — concepts and, with attribution, text |
| [addyosmani/agent-skills](https://github.com/addyosmani/agent-skills) | `2471e3f` | 2026-07-25 | MIT (Addy Osmani, 2025) | Yes — concepts and, with attribution, text |
| [DietrichGebert/ponytail](https://github.com/DietrichGebert/ponytail) | `16f2980` | 2026-07-15 | MIT (DietrichGebert, 2026) | Yes — concepts and, with attribution, text |
| [techygarg/lattice](https://github.com/techygarg/lattice) | `75b7e07` | 2026-07-06 | MIT (Rahul Garg, 2026) | Yes — concepts and, with attribution, text |
| [open-gsd/gsd-core](https://github.com/open-gsd/gsd-core) | `46ba02a` | 2026-07-26 | MIT (Open GSD, 2026) | Yes — concepts and, with attribution, text |
| [satococoa/wtp](https://github.com/satococoa/wtp) | `842920d` | 2026-03-09 | MIT (Satoshi Ebisawa, 2024) | Yes — concepts and, with attribution, text |
| [headroomlabs-ai/headroom](https://github.com/headroomlabs-ai/headroom) | `b121223` | 2026-07-25 | **Apache-2.0** | Yes — Apache-2.0 notice/attribution obligations apply |
| [Graphify-Labs/graphify](https://github.com/Graphify-Labs/graphify) | `66d8110` | 2026-07-25 | **Apache-2.0** (+ `LICENSE-MIT` for pre-relicensing contributions; `NOTICE` present) | Yes — must preserve `NOTICE` if any file is vendored |
| [Dicklesworthstone/beads_rust](https://github.com/Dicklesworthstone/beads_rust) | `94fb146` | 2026-07-22 | **MIT with OpenAI/Anthropic Rider** | **Restricted — see §2** |
| [gastownhall/beads](https://github.com/gastownhall/beads) | `d01d62e` | 2026-07-25 | MIT (Beads Contributors, 2025) | Yes — concepts and, with attribution, text |
| [gastownhall/gastown](https://github.com/gastownhall/gastown) | `649b832` | 2026-07-23 | MIT (Steve Yegge, 2025) | Yes — concepts and, with attribution, text |
| [first-fluke/oh-my-agent](https://github.com/first-fluke/oh-my-agent) | `2c28bc4` | 2026-07-30 | MIT (Eunkwang Shin and Gahyun Kim, 2026) | Yes — concepts and, with attribution, text |
| [code-yeongyu/oh-my-openagent](https://github.com/code-yeongyu/oh-my-openagent) | `f287227` | 2026-07-30 | **Sustainable Use License 1.0** — non-OSI, non-commercial-only (no holder named; some `packages/*` subtrees separately MIT, Yeongyu Kim, 2026) | **Restricted — see §2.2** |
| [openai/symphony](https://github.com/openai/symphony) | `f8e8b8a` | 2026-07-24 | **Apache-2.0** (stock text; `NOTICE` present — "Copyright 2025 OpenAI") | Yes — must preserve `NOTICE` if any file is vendored |
| [automazeio/ccpm](https://github.com/automazeio/ccpm) | `7d7e462` | 2026-03-18 | MIT (Ran Aroussi, 2025) | Yes — concepts and, with attribution, text |
| [coleam00/Archon](https://github.com/coleam00/Archon) | `3044829` | 2026-07-30 | MIT (Cole Medin, 2025-2026) | Yes — concepts and, with attribution, text |
| [SouthBridgeAI/hankweave-runtime](https://github.com/SouthBridgeAI/hankweave-runtime) | `66a9921` | 2026-07-20 | **Apache-2.0** (stock text) **+ `NOTICE.md` Terms-of-Service incorporation with competition restrictions** | **Restricted — see §2.3** |

The Apache-2.0 repos are usable but carry obligations MIT does not: retain the licence and
`NOTICE`, state changes, and do not use the project's marks. Since we intend to take *concepts*
from them (headroom's cache-alignment idea, graphify's edge-provenance labels) rather than code,
the practical obligation is attribution in the design doc — which §4 of the review provides.

**Rows 11–17 were added 2026-07-30**, after the review sweep of the same date. No licence file was
missing in any of the seven. Five are stock and unremarkable; two are not, and in both cases **the
`LICENSE` file alone would have cleared them** — which is precisely the failure mode §2 was written
about. That is now three restricted repos found in this set, so treat "read the LICENSE" as the
*start* of the check and not the whole of it: read every licence-bearing file, including `NOTICE`,
and read the per-subdirectory licences before assuming a monorepo is uniform.

## 2. Restricted licences, and the corrections each forced

Three of the seventeen reviewed repos restrict what we may do, and in every case a casual read of
the licence would have missed it. Each subsection below records the operative clause verbatim, the
consequence, and the claim in our own documents it invalidated.

### 2.1 `beads_rust` — the OpenAI/Anthropic rider

`docs/design/work-tracker.md` §7 asserted: *"Reading beads_rust and bv sources for reference is
explicitly sanctioned while they are MIT."* **That statement was factually wrong** and has been
corrected in that document.

`beads_rust/LICENSE` is titled `MIT License (with OpenAI/Anthropic Rider)`. The rider:

- Defines "Restricted Parties" as OpenAI, Anthropic, their affiliates, **and any person or
  entity acting directly or indirectly on behalf of, for the benefit of, or under the direction
  of any of the foregoing.**
- Grants **no rights** to any Restricted Party, and voids any purported sublicense to one.
- Defines restricted "use" to include, verbatim, *"benchmarking, testing, analyzing, indexing,
  or incorporating the Software or any Derivative Works into any dataset, training corpus,
  evaluation harness, or pipeline for machine learning or other automated systems."*
- States that breach terminates the licence immediately and that the rider must be reproduced
  unmodified in any distribution of the Software or a derivative work.

Three consequences, stated plainly:

1. **The rider is at minimum ambiguous as applied to an Anthropic model reading the source at a
   user's direction**, and it explicitly names "analyzing" as restricted use. This review
   therefore **did not read `beads_rust` source**. Everything the review says about it comes
   from its published `README`/docs — which describe an interface we already consume — or from
   the observable behaviour of the `br` binary we already run.
2. **A clean-room boundary now applies to the tracker work.** `work-tracker.md`'s replacement
   tracker must not be derived from `beads_rust` source. Its legitimate inputs are: our own
   ledger's observable data, `br`'s documented CLI contract, and `gastownhall/beads` (genuine
   MIT), which covers the same conceptual ground and is the upstream original.
3. **This is also a supply-chain argument, not only a legal one.** A dependency whose licence
   can be amended with a rider aimed at a class of users is exactly the "unowned dependency in
   our critical path" risk `work-tracker.md` §1 was written about. The finding strengthens that
   document's thesis rather than weakening it.

Not legal advice. If the tracker work proceeds to implementation, the boundary above should be
confirmed by someone qualified; until then the conservative line costs us nothing, because the
MIT original is available and is the better reference anyway.

### 2.2 `oh-my-openagent` — not open source at all

`LICENSE.md` is the **Sustainable Use License 1.0**, which is not an OSI-approved licence. The
operative limitation, verbatim:

> You may use or modify the software only for your own internal business purposes or for
> non-commercial or personal use. You may distribute the software or provide it to others only if
> you do so free of charge for non-commercial purposes.

It also requires that *"anyone who gets a copy of any part of the software from you also gets a copy
of these terms"*, and that a modified copy carry a prominent notice of modification.

**Monorepo subtleties that matter, because the safe-looking path is narrower than it appears.** Some
`packages/*` subtrees carry their own MIT (`pi-goal`, `pi-webfetch`, `lsp-tools-mcp`), and
`omo-senpi/plugin/LICENSE` is a *scoped* MIT covering six named portions only. **`packages/model-core`
has no licence of its own**, so the root Sustainable Use License governs it — and `model-core` is
exactly where the three files the review cites live
(`model-resolution-pipeline.ts`, `category-model-requirements.ts`, `model-settings-compatibility.ts`).

**Consequence, and a correction to our own review.** Review §2.12 as first written recommended that
tier-routing logic as *"about 400 lines of pure logic that ports to stdlib Python unchanged"*. Under
this licence that is not an available option: `basicly` is distributed, so a port would be
distribution of a derivative work outside the permitted purposes. The recommendation is withdrawn
there and on `basicly-kjc5.58`.

**What survives is the part that was always the valuable part.** A licence restricts copying
expression, not learning a fact. The *concept* — a work item declares a named capability tier rather
than a model id; the resolver returns provenance for which rule chose the model; an unsupported
setting is clamped and the downgrade recorded rather than refused — is an idea, and the observation
that their HEAD makes tier and model id mutually exclusive is a fact about published history. Both
stay usable. What stops is treating their source as the implementation reference: no port, no
snippet, no line-by-line transcription. Same clean-room posture as §2.1.

### 2.3 `hankweave-runtime` — a competition restriction asserted through `NOTICE.md`

The `LICENSE` is stock Apache-2.0. The restriction is in `NOTICE.md`:

> By using Hankweave, you agree to Southbridge AI's Terms of Service:
> `https://www.southbridge.ai/blog/terms-of-service`
>
> Key provisions include:
>
> - You retain ownership of your Hanks and Outputs
> - **Competition restrictions on using Hanks to build competing products**
> - Managed services restrictions require prior written consent

**Why this plausibly reaches us.** `basicly` is a coding-agent harness with an orchestration engine;
hankweave is an agent-orchestration runtime. Those are adjacent enough that "competing product" is
not obviously inapplicable, and the review used hankweave specifically as prior art for an
orchestration component we intend to build.

**Genuinely unsettled, and not ours to settle.** Apache-2.0 treats `NOTICE` contents as
informational and does not provide for a NOTICE adding terms; whether an incorporated Terms-of-Service
can bind a recipient who merely reads a public repository is a legal question. Two readings are
available and we are not qualified to choose between them. **So the conservative line applies, and it
costs little:** treat derivation from hankweave *source* as out of bounds pending review by someone
qualified, and stop at its published `README` and docs.

**Consequence for `basicly-vkh0.9`.** That bead was filed recommending we absorb their journal
mechanisms with source line ranges as the reference. Narrowed: the **measurements** stay — that 44.5%
of events in their own committed fixture share a millisecond is a measured property of published
data, and it is the finding that turns `work-tracker.md` §9.5 from an assertion into evidence. The
mechanism adoption is held pending the licence question.

Note also, for a different reason: their `NOTICE.md` records that hankweave orchestrates Claude
through `@anthropic-ai/claude-agent-sdk`, which it states is **not** open source. Irrelevant to our
own dependency policy, but it is the second time in this set that a project's real constraints lived
outside its `LICENSE`.

## 3. Primary documents read, by repo

Listed so a later reader can go straight to the source of a finding rather than re-deriving it.

**mattpocock/skills** — `README.md`, `CLAUDE.md`, `CONTEXT.md`, `.agents/invocation.md`;
skills: `productivity/writing-great-skills/{SKILL.md,GLOSSARY.md}`, `productivity/grilling`,
`productivity/handoff`, `engineering/code-review`, `engineering/tdd`, `engineering/to-tickets`,
`engineering/implement`, `engineering/wayfinder`, `engineering/codebase-design`,
`engineering/diagnosing-bugs`, `engineering/triage`,
`engineering/improve-codebase-architecture`, `engineering/research`,
`engineering/resolving-merge-conflicts`.

**obra/superpowers** — `README.md`, `hooks/hooks.json`; skills: `using-superpowers`,
`subagent-driven-development`, `dispatching-parallel-agents`, `writing-plans`,
`verification-before-completion`, `requesting-code-review`, `receiving-code-review`,
`brainstorming`, `writing-skills`.

**addyosmani/agent-skills** — `evals/README.md`, `evals/cases/code-review-and-quality.json`,
`references/orchestration-patterns.md`, `skills/doubt-driven-development/SKILL.md`; layout of
`agents/`, `commands/`, `hooks/`, `references/`, `docs/`.

**DietrichGebert/ponytail** — `README.md`, `docs/agent-portability.md`,
`skills/ponytail/SKILL.md`, `skills/ponytail-debt/SKILL.md`,
`benchmarks/results/2026-06-18-agentic.md`, `benchmarks/` layout.

**techygarg/lattice** — `README.md`, `docs/framework-intelligence.md`; layout of
`skills/{atoms,molecules,refiners}`, `dev-skills/`, `plugins/`.

**open-gsd/gsd-core** — `README.md`, `docs/explanation/context-engineering.md`,
`docs/explanation/the-phase-loop.md`, `docs/explanation/multi-agent-orchestration.md`,
`gsd-core/references/gates.md`, `agents/gsd-plan-checker.md`, `agents/gsd-nyquist-auditor.md`.

**satococoa/wtp** — `README.md`, `docs/` layout, `internal/` layout.

**headroomlabs-ai/headroom** — `README.md`, `crates/` layout, `docs/` layout.

**Graphify-Labs/graphify** — `README.md`, `ARCHITECTURE.md`, `NOTICE`.

**gastownhall/beads** — `README.md`, `docs/architecture/{index,dolt}.md`,
`docs/core-concepts/{hash-ids,adaptive-ids,sync-concepts}.md`,
`docs/multi-agent/coordination.md`.

**Dicklesworthstone/beads_rust** — `LICENSE` only (see §2).

## 4. Web sources

Confidence is graded because it matters: a repo read at a pinned SHA is near-certain, a
first-party vendor doc is strong, and a PDF summarised by a small extraction model is a lead to
verify, not a citation to lean on.

| Source | What it gave us | Confidence |
| --- | --- | --- |
| [Steering Claude Code: skills, hooks, rules, subagents and more](https://claude.com/blog/steering-claude-code-skills-hooks-rules-subagents-and-more) | The seven steering mechanisms, their load timing, compaction behaviour, context cost and authority; the "every time X → use a hook" decision rule | **High** — first-party vendor doc |
| [How to write a great agents.md: lessons from over 2,500 repositories](https://github.blog/ai-and-ml/github-copilot/how-to-write-a-great-agents-md-lessons-from-over-2500-repositories/) | Six recurring sections; commands-early ordering; one-code-example-beats-three-paragraphs; the always / ask-first / never boundary triad; "never commit secrets" as the most common useful constraint | **High** — first-party, large sample |
| [GitHub Docs: custom instructions for Copilot code review](https://docs.github.com/en/copilot/tutorials/customize-code-review) and [Unlocking the full power of Copilot code review](https://github.blog/ai-and-ml/github-copilot/unlocking-the-full-power-of-copilot-code-review-master-your-instructions-files/) | `*.instructions.md` + `applyTo:` glob frontmatter as the path-scoping mechanism; a file without `applyTo` does nothing automatically | **High** — first-party |
| [AGENTS.md](https://agents.md/) | The cross-agent instruction-file convention basicly already projects to | **High** |
| [CLAUDE.md token budget optimization](https://thepromptshelf.dev/blog/claude-md-token-budget-optimization/), [CLAUDE.md best practices](https://techsy.io/en/blog/claude-md-best-practices), [Claude Code anti-patterns](https://www.aicodex.to/articles/claude-code-antipatterns) | The adherence-decay thresholds (~80 lines rules start dropping, ~200 lines blocks ignored, ~500 words of dense rules adherence collapses); "a rule with a reason generalises, a rule without one is dropped when context shifts"; the new-session "summarise the rules" self-test | **Medium** — consistent across independent write-ups but no primary experiment published; treat the numbers as an order of magnitude, not a constant |
| [Refute-or-Promote: adversarial stage-gated multi-agent review](https://arxiv.org/pdf/2604.19049) | Findings must survive an explicit refutation stage; 3–5 independent refuters vote; adversarial framing rather than "is this good?" | **Medium** — PDF summarised by an extraction model; the mechanism is corroborated by `doubt-driven-development` and `gsd-plan-checker`, the reported numbers are not verified |
| [Harness as an Asset: enforcing determinism via CAAF](https://arxiv.org/pdf/2604.17025) | Two named failure modes we lacked names for: **compliant hallucination** (output satisfies the constraint while defeating its purpose) and **stochastic oscillation in reflection loops** (a review loop cycling without converging) | **Medium** — same caveat; the two failure-mode names are the durable takeaway |
| [Agentic Harness Engineering: observability-driven evolution of coding-agent harnesses](https://arxiv.org/pdf/2604.25850) | Harness components can be improved measurably without changing the model; evolution is driven by execution-trace signals | **Low-Medium** — summary was generic; directionally supports our telemetry work, cite nothing specific from it |
| [Addy Osmani: Agent Harness Engineering](https://addyosmani.com/blog/agent-harness-engineering/) and [awesome-harness-engineering](https://github.com/ai-boost/awesome-harness-engineering) | "Harness over model" framing; the harness validates the tool call rather than the model calling tools directly; full-context-reset-from-a-handoff-file for long jobs | **Medium** — practitioner synthesis |
| [Adversarial Code Review: why the maker shouldn't grade the checker](https://www.augmentcode.com/guides/adversarial-code-review) | Read-only checker + separate fixer as a permissions pattern | **Medium** |
| [forrestchang/andrej-karpathy-skills](https://github.com/multica-ai/andrej-karpathy-skills) (MIT, created 2026-01-27; widely forked) | Four behavioural principles: think-before-coding (surface assumptions), simplicity-first, **surgical changes** (touch only what you must; clean up only your own mess), goal-driven execution (define success criteria, loop until verified) | **Medium-High** for the content (read from the repo); **Low** for the popularity claims — aggregator blogs report both "144K" and "101K" stars, so cite the artifact, never the count |
| [The `/grill-me` skill](https://www.aihero.dev/skills-grill-me) and [azukiazusa's write-up](https://azukiazusa.dev/en/blog/before-implementation-interview-design-requirements-grill-me/) | The grilling contract: one question at a time, always carry a recommended answer, look facts up rather than asking, walk the decision tree depth-first | **High** — matches the primary source in `mattpocock/skills` |
| curl's bug-bounty closure after AI-submitted reports drove the confirmed rate below 5% (reported in the code-review-agent search results) | The empirical cost of an unverified finding stream | **Low-Medium** — second-hand; useful as an illustration, not as a statistic to quote |

## 5. What was deliberately not done

- **No `beads_rust` source read** (§2).
- **No vendoring.** Nothing from any repo above has been copied into `basicly`. Every finding in
  the review is expressed in our own words against our own design.
- **No implementation.** The review's output is design documents. Nothing in `src/` changed.
- **The arxiv papers were not read in full.** They were fetched and summarised; the PDFs are
  cached under this session's tool-results directory. Where a paper's contribution mattered
  (CAAF's two failure-mode names, Refute-or-Promote's stage gate) the same mechanism was
  independently corroborated in a repo we did read, and the review leans on the repo.

## 6. Re-running this review

```sh
# from the workspace root
mkdir -p reference-repos && cd reference-repos
for u in mattpocock/skills obra/superpowers addyosmani/agent-skills \
         DietrichGebert/ponytail techygarg/lattice open-gsd/gsd-core \
         satococoa/wtp headroomlabs-ai/headroom Graphify-Labs/graphify \
         gastownhall/beads ; do
  git clone --depth 50 "https://github.com/$u.git" "$(basename "$u")"
done
```

`Dicklesworthstone/beads_rust` is deliberately omitted from that loop; clone it only if you have
resolved §2, and read `LICENSE` first.

These projects move fast — `gsd-core` landed a commit the same day this review was written, and
`superpowers` shipped a minor release three days before. Treat every finding as pinned to the
revision in §1 and re-check before acting on one that has been sitting for a while.

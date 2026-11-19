# Opus 5.5 prompting pass over the catalog

Record: basicly-3i052o2. Pass run on 2026-11-19.

## Result

No catalog text breaks a rule of the Opus 5.5 prompting guide, so this pass fixes no wording. Two findings would change behaviour, so they go to the orchestrator as proposals and are not changed here. The best-practices audit now fetches the guide and checks six of its rules.

## Source

- Vendor page: <https://platform.claude.com/docs/en/build-with-claude/prompt-engineering/prompting-claude-opus-5-5.md>, HTTP 200, read 2026-11-19 10:23:42 UTC. The page shows no last-updated date.
- The page has 31 rules (O1 to O31). Most of them set API parameters: effort level, `max_tokens`, thinking display, and turn-scoped system messages. A catalog source cannot set those.
- The page has no rule on capital-letter emphasis, XML tags in general, skills, agent definitions or prompt length. A count of those words in the page gave 0. Words known to be on the page (`tag`, `example`, `subagent`) were found, so the count works.

## Scope

`.basicly/core/agents`, `.basicly/core/skills` (without `evals.yaml`), `.basicly/core/fragments`, `.basicly/core/output-styles` and `packages/basicly-orch`. The searches ran in lane `harness/dev-orch-cut4` at 1a8b3bac, which holds this catalog plus the orchestration kit text, including the `orchestrated-session` fragment (1067 tokens).

## Rules checked

| Rule | Vendor text (quoted) | Search | Hits | Finding |
| --- | --- | --- | --- | --- |
| O24 | "if your system prompt contains instructions that tell Claude to think carefully before answering, consider removing them for Claude Opus 5.5." | `think (carefully\|hard\|deeply\|step by step)\|think before` | 0 | none |
| O7, O15 | "If your prompts ask the model to write its reasoning in the response, remove those instructions" | `(write\|show\|explain\|state) (your\|the) reasoning` | 0 | none |
| O6 | "a system prompt line such as "Answer directly without deliberating." can reduce thinking further" | `answer directly\|without deliberating` | 0 | none |
| O25, O26 | "Leave it out where you want the model to keep re-examining its earlier work" | `treat (earlier\|previous) answers as\|do not revisit` | 0 | none |
| O20 | "on loosely specified tasks it helps to tell the model to look through the relevant sources before acting." | `look through\|read the relevant sources first` | 0 | F1 |
| O12 | "Claude Opus 5.5 is responsive to instructions that name the specific kinds of early stop you want it to avoid" | `don't stop\|do not stop\|stop when\|before you stop` | 3 | F2 |
| O27 | "Wrap each pasted block in an opening and a closing tag that both carry the same short random ID" | `pasted` | 0 | none: the host adds these tags, not the catalog |
| O10 | "Keep the task's parts in a checklist the model updates, such as a to-do tool or a file." | `checklist\|to-do` | 14 | none: the skills that run long tasks already keep a checklist |
| O14 | "keep your own confirmation step for risky or irreversible actions" | ask-first list in the always-on files | present | none |
| Control | a word known to be in the scope | `tracker` | 158 | the searches work |

## Findings that change behaviour (to the orchestrator, not fixed here)

- **F1 (O20).** No agent or fragment tells the model to read the relevant sources before it acts on a loosely specified task. The core rule "search the code that would call the capability" covers code only. Proposal: one line in the worker always-on rules. Effect: every session reads more before it acts.
- **F2 (O12).** The three hits name when a script or the user ends a loop (`.basicly/core/skills/hill-climb/skill.yaml:47`, `.basicly/core/skills/skill-creator/references/improvement.md:36`, `_round_refusal`, `.basicly/core/skills/swarm/scripts/swarm.py:281`). No always-on rule names the early stops a worker should avoid, such as ending a turn with open items and no stated blocker. Proposal: one line in the orchestrated-session fragment or the session-finish skill. Effect: workers continue where they now stop.

## What this pass does not establish

- Whether the rules on the linked general pages ("Prompting best practices", the Opus 5 guide) add rules for catalog text. They were not fetched.
- The effect of either proposal. Each needs a measured run before it ships.

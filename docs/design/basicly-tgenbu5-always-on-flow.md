# Always-on order and core budget

A consumer repository keeps 50 of the 200 lines that Anthropic recommends for a
`CLAUDE.md`. So the shipped core renders at most 150 lines on every target, and
`tests/test_baseline_retrieval.py` refuses a larger core.

## Admission rule

An instruction stays always-on only when almost every task needs it and a missed rule
causes a real mistake. Any other instruction moves into a skill that the agent loads on
demand, behind a one-line trigger, or it leaves the catalog.

## Order

The retention measurement in `targets/claude.yaml` loses the tail of a file. The earlier
layout put Never do and Ask first at the tail, so the gates, the security rules and the
confirmation list were the rules most at risk. The new layout is:

1. Never do: the gate rule, the comment ban and secure coding.
2. Ask first: the confirmation list.
3. Commands: the consumer's own commands.
4. Always do, in workflow order. A fragment without a `phase` comes first, so a consumer's
   own facts lead. Then `start`, `plan`, `build`, `verify`, `ship` and `finish`.

| Phase | Core fragments |
| --- | --- |
| start | knowledge-priming, harness-loop |
| plan | decision-protocol, external-facts |
| build | core-rules, mistake-proofing, catalog-authoring, non-interactive-shell |
| verify | quality-gate |
| ship | git-discipline |
| finish | session-completion |

## Instruction accounting

The guidance tests pin the decisions of `basicly-a3ab.4`. The always-on decisions stay
verbatim: approval reuse, frozen scope, read-only clarification, task authorization, gate
protection, evidence reuse and the absence controls. The table lists each instruction that
changed place or left the baseline.

| Instruction | Disposition | Destination |
| --- | --- | --- |
| Gate rule after the comment ban | moved | First paragraph of Code Is Authoritative |
| Session handover note | moved | Session Completion |
| Missing Lefthook runtime repair | routed | The hook error names the repair (`.basicly/core/hooks/lefthook-runtime.py`, `tests/test_hook_runtime_recovery.py`) |
| `uv run basicly` in a source checkout | routed | The Commands fragment of the basicly repository |
| Architecture index use | shortened | Knowledge Priming reads the relevant architecture sections |
| Full inventory for a repository-wide audit | routed | `falsify-first`: name the list that the code reads |
| Poka-yoke lead sentence | merged | First Mistake Proofing bullet |
| Routine observed count names its source | deduplicated | Core Rules: back every claim with evidence |
| Mark a claim from unread code as an assumption | moved | Core Rules |
| Two-path derivation of a high-risk number | routed | `falsify-first`, behind the External Facts trigger |
| Two causes with the same prediction | routed | `falsify-first` |
| Complete coverage names the list the code reads | routed | `falsify-first` |
| Check a claim against code before a plan or README | routed | `falsify-first` and `validate-as-consumer` |
| Account for every file of a retired source system | routed | `falsify-first` |
| Long-run usage report | routed | `session-finish` |
| L-level grant wording | shortened | Harness Loop says "Do not request a grant"; `harness-loop` refuses a synthetic grant |
| Example commit subject | removed | The `conventional-commits` skill and the `commit-msg` hook carry the format |
| Interactive alias reason | removed | The flag list stays and carries the action |
| Catalog authoring steps | routed | `catalog-authoring`, behind a one-line trigger |
| Claude Notes (`claude-defaults`) | routed | `catalog-authoring`, fragment deprecated |
| Copilot Notes (`copilot-defaults`) | routed | `catalog-authoring`, fragment deprecated |
| Platform-Hermetic Tests | routed | `test-discipline`, which Core Rules links; fragment deprecated |
| No-comments skill detail list | shortened | Code Is Authoritative still links the skill |

The measured result for a fresh `basicly install`, counted with `wc -lm`:

| Target | Lines before | Lines after | Characters before | Characters after |
| --- | --- | --- | --- | --- |
| `.claude/CLAUDE.md` | 129 | 107 | 9297 | 7222 |
| `AGENTS.md` | 124 | 107 | 9124 | 7222 |
| `.github/copilot-instructions.md` | 130 | 107 | 9389 | 7222 |

The character cap of 12000 in `targets/claude.yaml` binds before the line cap. A consumer
keeps the difference for its own fragments.

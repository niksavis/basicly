# The `basicly` CLI

Every command the engine ships, one row per command or per command *group*, with the
behaviour a caller gets. This is the reference; the **why** stays in the specification.
[`architecture.md` §22](../architecture/architecture.md#22-the-cli-surface) holds the two
properties of this surface that are decisions, and this page holds the surface itself.

**Five gates read the tables below, against `cli._build_parser()` and in both directions.**
Two `docs_claims` assertions — `cli-commands` and `cli-subcommands` — refuse a shipped
subcommand that has no row, at pre-commit. Three `tests/test_docs_drift.py` tripwires refuse
the reverse at pre-push: a row naming a command nobody registers, and a `board serve` row
missing one of that command's flags.
[`conventions.md` §7](../architecture/conventions.md#7-which-gates-bind-on-which-document)
records which gate binds where.

**The whole file is the gated region**, unlike the section it replaced: a script reads every
table row here, takes the command from the first cell and nothing from the second. So a
command belongs in the first cell, and prose about it in the second.

**29 top-level commands. Ten of them are subcommand groups** [measured 2026-08-28, count
`subparsers(cli._build_parser()).choices`].

They fall into three surfaces.

| Surface | For | Commands |
| --- | --- | --- |
| lifecycle | a consumer repository | install, uninstall, status, health, brief |
| catalog | an author of catalog sources | build, check, the four build/check pairs, usage, catalog, rubric |
| harness | the development loop, in either repository | session, worktree, verify, policy, decompose, loop, commit, runner, tracker, board, release |

**Lifecycle.**

| Command | Behaviour |
| --- | --- |
| `basicly install` | Idempotent converge: materialize or sync the core, migrate legacy layouts, scaffold overlay and config without overwriting, then build, skills-build across all default roots, agents-build, hooks-build with activation. First install and every upgrade |
| `basicly uninstall [--purge]` | Remove everything managed, preserve the overlay and config unless purging, refuse in the authoring repo |
| `basicly status [--json] [--fleet]` | Read-only snapshot: installed catalog version against running engine version, drift summary, per-manager hook state, technology selection, overlay counts. Never writes, always exits zero. The fleet flag rolls it across the housed repositories as one JSON payload |
| `basicly health [--json] [--window N] [--fleet]` | Read-only per-agent health scoring and behavioural drift from the run-record log: dispatch failure rate, a rework signal, a bounded score, and a rolling-baseline drift flag. Never writes, always exits zero |
| `basicly brief <issue-id>` | Print the brief the loop would dispatch for one issue, without dispatching it. Shares the dispatch renderer rather than re-rendering, because a preview that differs from the dispatch is worse than none |

**Catalog.**

| Command | Behaviour |
| --- | --- |
| `basicly build [--target NAME] [--verify]` | Render enabled targets, write only changed bytes, update the manifest, warn on cap overrun. The verify flag runs the content checks first and writes nothing on failure |
| `basicly check` | Byte-for-byte staleness check of generated files and the manifest; exit 1 on mismatch, no auto-fix |
| `basicly skills-build [--root ...\|--all-default-roots]` / `skills-check` | The same build and check contract for the skill catalog, mirrored per root. Without a flag it writes one root only, which [14. Skills](../architecture/architecture.md#14-skills) marks as a target to fix |
| `basicly agents-build` / `agents-check` | The same contract for the agent catalog, always both roots, with no root-selection flag |
| `basicly hooks-build [--no-install]` / `hooks-check` | Materialize hook scripts, merge a managed block into the hook config preserving foreign hooks, then install the git hooks so the gates are active. The check reports projection drift and warns when the git hooks are not installed |
| `basicly permissions-build` / `permissions-check` | Project the agent-permissions deny-list into the co-owned settings file: ensure-present, consumer entries preserved, nothing pruned, with a semantic subset drift check |
| `basicly usage report` | The tool and skill counts the telemetry hook recorded, and the catalog skills never used: the culling input |
| `basicly usage forecast` | Forecast error per dispatch, over local run records and committed markers. Refuses to compute an error for a record missing either half and reports those as unpaired, so an empty report explains itself |
| `basicly usage tuning` | Advise every governed factory parameter from the recorded dispatches: the value in force, the outcome distribution under it, and a recommendation labelled measured or seeded. Advisory only; it writes nothing |
| `basicly usage lane-split` | Split each persisted lane transcript into a context-acquisition share and an implementation share |
| `basicly usage outcomes` | How every recorded dispatch ended, with the failure share as an explicit rate |
| `basicly usage tracker [--promote] [--refresh-surface] [--as-json]` | The measured external-tracker surface the replacement scope is frozen from |
| `basicly catalog list [fragment\|skill\|agent]` | Table of catalog sources of the given kind |
| `basicly catalog new <fragment\|skill\|agent> NAME [--category C] [--description D]` | Scaffold a new source in the correct format |
| `basicly catalog lint` | Source-format and composition gate; wired as a pre-commit hook and a CI step |
| `basicly catalog verify` | Deterministic content checks beyond the load path: duplicate bodies, contradictions, ambiguity, scope overlaps |
| `basicly catalog review [--runner NAME] [--dry-run]` | Advisory agent-assisted semantic review; always exits zero |
| `basicly catalog dump` | The composed selection the build would make: the technology axis and every fragment root in load order, each overlay-over-core override beside the core source it shadows, then every planned output with the axes it declares and every item it selected with that item's own origin |
| `basicly rubric eval <issue> [--runner NAME] [--dry-run]` | Evaluate the issue's work-type behavioural rubric: deterministic checks through the verify runner, judged checks through one agent prompt. Reports an advisory gate, promotable by naming it in the required set |

The names above are the whole authoring surface. Of the two planned reporting views, the conflict
one was cut from scope and the `basicly catalog verify` output covers that need; the override one
is `basicly catalog dump`.

**Harness.**

| Command | Behaviour |
| --- | --- |
| `basicly session start [--json] [--rows N]` | Read-only orientation for a session, with every line derived and none authored: the newest note tagged `[session handover <date>]` on whichever record carries it (where the last session stopped, or that no session said), the ranked ready set carrying the ranking policy that produced it, what is blocked and by what, every live grant with what is left of it where this checkout can see the spend, and the decision records whose status in [38. Decision records](../architecture/architecture.md#38-decision-records) is not `accepted`. An empty ledger says so rather than drawing an empty frame. Never writes, always exits zero |
| `basicly worktree create\|list\|cleanup` | Sibling worktree lifecycle: create provisions dependencies and installs the gates; cleanup removes the worktree and its merged branch |
| `basicly worktree merge\|merge-queue\|bg-isolation` | Land one finished worktree on its base; land several serially in a given topological order; turn off the host's own background isolation so the loop isolates itself |
| `basicly verify [--mode fast\|full\|staged] [--issue ID] [--gate NAME] [--fix]` | Run the consumer's configured checks for a mode and optionally record a tracker gate; the fix flag applies mechanical repairs first |
| `basicly policy dor\|scaffold\|gate\|rework` | Report the definition-of-ready, emit a body with every required heading, and read or record gate and rework state |
| `basicly policy checkpoint\|grant` | Approve a human checkpoint behind a terminal or a one-time confirm code; show, issue or revoke a session autonomy grant |
| `basicly decompose` | Turn a feature into child issues plus a computed dependency graph |
| `basicly loop status\|advance\|run <issue>` | Drive one issue through the loop; a blocked step exits nonzero and names the input it needs |
| `basicly loop preflight\|supervise\|stop` | The multi-lane path: preflight is read-only and reports clean base, live worktrees, runner, grant, budget and a per-lane band table; supervise dispatches ready lanes, routes their outcomes and lands green work; stop asks a running supervisor to finish the round it is in |
| `basicly loop session\|watch\|decisions\|answer\|decide\|kill` | A second session observes a live run and clears what a lane is blocked on. Answer records a human answer, decide invokes the confined decider agent, and kill closes a lane with a recorded reason behind a one-time confirm code that no grant and no terminal substitutes for |
| `basicly loop improve [--dry-run]` | The second loop shape, taking no issue: run the repository's improvement controller, which measures one declared property, selects one target deterministically and files at most one lane |
| `basicly commit <description>` | Assemble the conventional-commit envelope from engine state and commit the staged change. Only the description is authored; the commit-message hooks stay the gate |
| `basicly runner list\|dry-run\|run` | Agent-agnostic headless runner adapters; the dry run prints the exact command an adapter would execute before any live invocation |
| `basicly tracker ready\|blocked\|stats\|show\|list` | The backlog, read out of the owned ledger: ready is the ranked set that can be worked now, blocked names what holds each record that is not ready, stats totals the graph by status, and show and list read one record and the set. The engine resolves the ledger's location, so a consumer never retypes it |
| `basicly tracker write` | One human tracker write through the engine seam, so it lands on the store the engine reads rather than beside it |
| `basicly board --out FILE` | Write the harness board as one self-contained HTML page, with the `harness-board/v1` snapshot beside it as `board-snapshot.json`. The page references no external origin and every panel carries the snapshot's age, so a value is never drawn without it |
| `basicly board validate` | Read a board snapshot and say whether this consumer can render it. A major-version mismatch refuses; an unknown key is reported and admitted |
| `basicly board serve [--port N] [--bind ADDR] [--refresh S] [--no-actions]` | Serve the board for a wall display. GET reads; **one POST route** runs a `basicly` command an operator submitted, behind a one-time confirm code the operator types and this server never holds. `--no-actions` registers no action route and every POST is then 405 — the recommended flag for an unattended wall. `--bind` takes a literal IPv4 interface address, refusing a wildcard or a hostname, and defaults to the loopback. While a supervisor lock is fresh it serves that producer's snapshot bytes and folds nothing; otherwise it folds for itself on `--refresh` and keeps the result in memory. It takes no lock and writes no file, so it blocks no gate |
| `basicly release <version> --issue ID [--date D] [--dry-run] [--autonomous --root ID]` | Bump the single-sourced version, regenerate version-stamped projections in a fresh interpreter, rewrite install pins on the consumer surfaces, fold the per-lane changelog fragments into a dated section, commit, and create the annotated tag. **Never pushes** |

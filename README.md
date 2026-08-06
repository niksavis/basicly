<div align="center">

<img src="site/assets/logo.svg" alt="basicly logo" width="112">

# basicly

**A coding-agent harness that ships the workflow and the state, not just the instructions — one catalog projected into every agent's config, a deterministic loop that runs on it, and gates at commit time.**

[![latest release](https://img.shields.io/github/v/release/niksavis/basicly?label=release)](https://github.com/niksavis/basicly/releases/latest)
[![quality gates](https://github.com/niksavis/basicly/actions/workflows/quality-gates.yml/badge.svg)](https://github.com/niksavis/basicly/actions/workflows/quality-gates.yml)
[![projection gate](https://github.com/niksavis/basicly/actions/workflows/basicly.yml/badge.svg)](https://github.com/niksavis/basicly/actions/workflows/basicly.yml)
[![python 3.14+](https://img.shields.io/badge/python-3.14%2B-3776AB?logo=python&logoColor=white)](pyproject.toml)
[![license](https://img.shields.io/github/license/niksavis/basicly)](LICENSE)

</div>

## What is basicly?

Most agent harnesses ship instructions and skills. `basicly` also ships the
**workflow** and the **state** — a deterministic development loop, the work
graph it derives progress from, and git gates that hold whether or not the model
read the guidance.

Four pillars:

- **01 Guidance** — one versioned YAML catalog, projected into what each tool
  natively reads: `CLAUDE.md`, `AGENTS.md`, `.github/copilot-instructions.md`,
  skills, subagents, permissions. Edit one fragment and every target regenerates
  consistently. Your overrides live in an overlay that upgrades never touch.
- **02 Gates** — git hooks across pre-commit, commit-msg and pre-push, plus
  agent hooks and a verify pipeline. **Enforcement is at commit time, not only in
  CI**: a hook that refuses the commit is a different guarantee from a check that
  fails the build afterwards.
- **03 The loop** — a development process shipped as commands: intake, classify,
  decompose, build, verify, ship, teardown, retro — driven the same way under
  Claude, Codex or Copilot. Work builds in an isolated worktree; run one track at
  a time, or fan out parallel lanes behind a serial merge queue.
- **04 The work graph** — issues, dependencies, gate results, checkpoints and
  evidence live in a tracked graph. The loop keeps no side-state: the phase a
  track sits in is *derived* from that graph, so a session can crash, compact, or
  hand over to a different agent family mid-track and the next command picks it
  up by re-reading it.

**The pillars are not a bundle of files.** The loop, and every headless agent it
dispatches, reads the artifacts pillar 01 emits — so the config layer's output
*is* the orchestrator's contract. That is why the same track can start on one
agent and finish on another, and it is what a projector alone or an orchestrator
alone cannot offer.

## Quick start

### Install

basicly requires **Python 3.14+** — for the CLI and for the projected hook
scripts that run inside your repo; older interpreters are not supported.
Into any git repo, with [uv](https://docs.astral.sh/uv/) already on the machine:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.7.1 basicly install
```

No `uv` or Python yet? The bootstrap shim installs `uv` first, then runs the
same command:

```sh
curl -fsSL https://raw.githubusercontent.com/niksavis/basicly/main/.scripts/bootstrap.sh | sh
```

Windows (PowerShell):

```powershell
powershell -ExecutionPolicy Bypass -Command "irm https://raw.githubusercontent.com/niksavis/basicly/main/.scripts/bootstrap.ps1 | iex"
```

Pin `@v0.7.1` for reproducible installs, or track `@main` for the latest. To
pin through the shim, append `-s -- --ref v0.7.1` (POSIX) or download the
script and pass `-Ref v0.7.1` (PowerShell). Where `git` is unavailable
(proxied or air-gapped environments), every
[release](https://github.com/niksavis/basicly/releases) attaches a built
wheel: download it and run `uvx --from ./basicly-*.whl basicly install`.

### Upgrade

Re-run the install command with the new pin — install is idempotent and
converges the repo to the selected version. There is no separate `update`
command.

### Uninstall

One command removes everything basicly manages; your overlay and
`basicly.toml` survive:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.7.1 basicly uninstall
```

Add `--purge` to also remove the user overlay, `basicly.toml`, and the
scaffolded VS Code tasks/CI workflow (only when still unedited).

## What install gives you

- The managed core catalog under `.basicly/` (fragments, skills, subagents, hook
  scripts).
- A model map at `.basicly/core/models/` — a subagent source declares a portable
  model tier (`low`, `medium`, `high`, `maximum`) rather than a provider model id,
  and the map records which concrete model that tier is per vendor and per
  surface, with that surface's own published cost and token limits. Plain JSON
  beside its schema, so any tool can read it.
- Generated agent instruction files (`CLAUDE.md`, `AGENTS.md`,
  `.github/copilot-instructions.md`) rendered from shared fragments.
- Projected skills at `.claude/skills/` and `.agents/skills/`, and subagents at
  `.claude/agents/` and `.github/agents/`.
- Activated hooks across three surfaces: git stages (pre-commit, commit-msg,
  pre-push — wired through the [pre-commit framework](https://pre-commit.com),
  whose config file is fixed at `.pre-commit-config.yaml`; the *tool* is named
  pre-commit, the file is not limited to that stage), Claude Code agent hooks,
  and Copilot agent hooks.
- A beads issue-tracker workspace, VS Code tasks, and a CI gates workflow.

Customize via YAML fragments in `.basicly-local/fragments/user/` — install
never touches them. Scope the catalog to your stack with
`--technologies` (for example `--technologies python,zsh`).

### Committer requirements

The projected git hooks run `uv run python ...`, so **every committer** to a
basicly-managed repo needs [uv](https://docs.astral.sh/uv/) on `PATH` and
Python 3.14+ — not just the person who ran install. `basicly hooks-check`
diagnoses a missing uv before it bites at commit time. Using the harness loop
(`basicly loop`, shared worktree tracker) additionally needs a
redirect-capable [beads (`br`)](https://github.com/Dicklesworthstone/beads_rust) CLI —
0.2.16 is the known-good floor, and worktree provisioning verifies it.

## How it works

```mermaid
flowchart TB
    subgraph sources["One source of truth"]
        core[".basicly/core/<br>managed catalog"]
        local[".basicly-local/<br>your overlay"]
    end
    cli{{"basicly install / build"}}
    core --> cli
    local --> cli
    subgraph outputs["Projected into your repo"]
        agents["Agent instructions<br>CLAUDE.md / AGENTS.md<br>copilot-instructions.md"]
        skills["Skills<br>.claude/skills<br>.agents/skills"]
        hooks["Hooks<br>git stages<br>Claude Code / Copilot"]
    end
    cli --> agents
    cli --> skills
    cli --> hooks
    gate["basicly check<br>drift gate, run by CI"] -. verifies .-> outputs
    loop{{"basicly loop / supervise"}}
    outputs -- "the contract the agents read" --> loop
    tracker[("work graph<br>issues, gates, evidence")]
    loop <-- "reads phase, writes gates" --> tracker
```

The edge from the projected outputs into `basicly loop` is the part that is easy
to miss, and it is drawn solid because it is load-bearing rather than advisory:
the loop does not carry its own copy of the rules. It dispatches headless agents
that read the same projected artifacts, so **the projection is the contract** —
and because the loop stores no side-state, phase is always re-derived from the
work graph.

The full design — directory contract, catalog model, verification pipeline —
lives in [`docs/architecture/architecture.md`](docs/architecture/architecture.md).

## Everyday commands

Day-to-day use needs nothing beyond `install` above. The scaffolded VS Code
tasks wrap the same pinned commands. To inspect or re-sync by hand, run these
from the consumer repo root with the same pin used to install:

```sh
uvx --from git+https://github.com/niksavis/basicly@v0.7.1 basicly check   # exit non-zero when generated files drifted
uvx --from git+https://github.com/niksavis/basicly@v0.7.1 basicly build   # regenerate agent instruction files
```

## Roadmap

There are no dates here on purpose — this is a status map, not a schedule. Every
capability sits under the pillar it belongs to, grouped by what it currently is:

- `✓` **shipped** — running code in the current release, exercised on this repo's
  own development.
- `▶` **building** — sequenced into a phase being worked now, with an open work
  package and written exit criteria.
- `◇` **designed** — settled in a design document but sequenced behind a later
  phase, and **nothing is built**; a recorded decision is not evidence that anything
  enforces it.
- `?` **researching** — the deliverable is a number, not a capability: a measurement
  whose result is allowed to cancel the work.

| Pillar | `✓` shipped | `▶` building | `◇` designed · `?` researching |
| --- | --- | --- | --- |
| **01 · guidance** | one catalog → 3 agent families<br>drift gate in CI<br>path-scoped rules tier<br>invocation axis per entry<br>model tiers · committed model map | lexical routing evals<br>an eval case per entry<br>always-on baseline relief<br>tutorial and how-to layer | `?` do entries change behaviour |
| **02 · gates** | git hooks · commit · push<br>agent hooks · Claude · Copilot<br>verify pipeline · 3 modes | gate taxonomy by type<br>severity on judged output<br>rework convergence check<br>install reports its tier | — |
| **03 · the loop** | single-track loop<br>worktree isolation<br>parallel lanes · merge queue<br>autonomy grants · spend cap<br>release automation<br>scope sized by what a lane reads<br>measured context per dispatch | per-model spend forecast<br>unattended multi-lane run | `◇` a named role per judgment step<br>`?` cost per landed package<br>`?` deterministic AST localisation |
| **04 · the work graph** | issues · deps · gates<br>phase derived from state<br>atomic shared-export publish | dispatch score recorded | `◇` owned in-process event log<br>`◇` provenance on every edge<br>`◇` fsck and rebuild |

Some things are **not planned**, so absence here is not an oversight: an LLM
orchestrator in control of the tracker, an agent-writable catalog, a maintained TUI,
an external database or daemon, and agent-to-agent messaging. The reasons are in
architecture §14.7.

The authoritative copy of this table — with the evidence each status requires and a
pointer per row — is
[architecture §15](docs/architecture/architecture.md#15-roadmap--status-per-capability).
The order the unbuilt rows get built in, with dependencies and exit criteria, is
[`docs/plan/implementation-plan.md`](docs/plan/implementation-plan.md). Both are
updated as features land.

## Contributing

Bug reports and ideas are welcome as GitHub issues. For development setup,
contributor commands, commit conventions, and the quality gates a change must
pass, see [`CONTRIBUTING.md`](CONTRIBUTING.md).

## License

Released under the [MIT License](LICENSE).

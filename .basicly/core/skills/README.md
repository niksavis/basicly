# basicly skill collection

This directory is the source-of-truth skill catalog for coding-agent enablement.

- Source shape: `.basicly/core/skills/<skill-name>/skill.yaml`
- Projection command: `PYTHONPATH=src uv run python -m basicly.cli skills-build`
- Default projection root: `.claude/skills`

## Catalog skills

Every source in this directory, with its own routing fields — generated and gated by
`.scripts/docs_claims.py`. A technologies-tagged source ships only to a repo that
selects that tag (`[catalog] technologies` in `basicly.toml`), so this is the catalog,
not the projection of any one consumer. A user-invoked source carries no description
by design: that absence is what keeps it out of the model's always-loaded index.

<!-- docs-claims:begin catalog-skills -->

| Skill | Invocation | Technologies | Description |
| --- | --- | --- | --- |
| `catalog-authoring` | `model` | any | Author and improve basicly catalog sources — skills and fragments — in their YAML source format (never a discoverable .md), then project and verify them. Use when adding or editing a skill or fragment, building a catalog, or deciding where guidance should live (always-on fragment vs on-demand skill). |
| `conventional-commits` | `model` | any | Construct a valid Conventional Commits message for this repo before running `git commit`, covering type/scope, the "!" breaking-change marker, description rules, and the required trailing beads issue id. Use whenever writing or reviewing a commit message, or when a commit is rejected by the commit-msg/beads-commit-msg hooks. |
| `harness-client` | `model` | any | Attach to a running basicly supervisor as a second session — observe live status, present its pending decisions to a human conversationally, and record the answers. Use when a supervisor is already running (or may be) and you are not the one driving it, to check what the factory is doing, unblock a lane that waits on a judgment, or answer a queued decision. |
| `harness-loop` | `model` | any | Drive a unit of work through the basicly harness loop end-to-end (intake → classify → decompose → build → verify → ship → teardown → retro) using `basicly loop` + `br`, agent-agnostic across Claude/Codex/Copilot. Use when starting or resuming non-trivial development in a harness-enabled repo, when deciding what phase a tracked issue is in, or when coordinating the checkpoints, gates, and bounded rework the loop enforces. |
| `interface-facts` | `model` | any | Establish a fact about an external interface - a CLI flag, an API field, a model id, a price, a limit, a version - by fetching the vendor's current documentation instead of recalling it. Use before writing code, a design note, or any claim that depends on how a third-party tool behaves, and whenever a repo document already asserts such a behaviour. |
| `node` | `model` | `node` | Use Node and npm in this repo for the markdownlint git hook and other node tooling. Use when running npm or npx committing or pushing from a script or background job on WSL or debugging a node-based hook that resolves the wrong node binary. |
| `python` | `model` | `python` | Write and edit Python for this repo — type hints pathlib and cross-platform subprocess and shell-out. Use when creating or changing .py files wiring up a subprocess call chasing a test that passes on POSIX but fails only on Windows CI (a WinError 2 or a mangled backslash path) or second-guessing syntax that looks wrong for an older Python (an unparenthesized multi-exception except clause). |
| `python-guidelines` | `model` | `python` | Make the design calls no linter can check — where an oversized file splits, whether a name or docstring carries meaning, whether an abstraction earns its keep, when a suppression is legitimate, and how to satisfy a size or complexity ratchet without gaming it. Use when a size or complexity gate has just failed, before adding a noqa or nosec, when deciding what to raise and what to catch, or when shared state is reached from more than one concurrent lane. |
| `release-process` | `model` | any | Cut a release of this repository with `basicly release`, then do the two steps that command deliberately leaves to a human - deciding the version and pushing. Use when asked to cut a release, tag a version, prepare release notes, or check whether a release published. |
| `session-finish` | `model` | any | Close out a working session with a usage-statistics report, a self-improvement retro, and a pickup-clean handoff summary. Use when the user says the session is done ("wrap up", "finish the session", "close out"), before ending a long autonomous run, or whenever a summary of what changed and what the agent actually used is wanted. |
| `test-discipline` | `model` | any | Write isolated order-independent automated tests that assert on observable behavior rather than private internals. Use when writing reviewing or debugging any test (unit integration or end-to-end) in any language especially when tests share fixtures touch global or filesystem state flake depending on run order or reach into implementation details. |
| `tier-injection` | `model` | any | Install the portable tier injection kit so a subagent spawns on the model its declared tier resolves to, instead of the host default. Use when setting up tier injection in this or another repository, when a subagent ignores the tier its definition declares, or when deciding whether a host can pin a spawn's model at all. |
| `tool-ast-grep` | `model` | any | Use ast-grep for structural code search and rewrite based on syntax trees. Trigger when text search is too imprecise for code-aware matching. |
| `tool-bat` | `user` | any |  |
| `tool-br` | `model` | any | Use br (beads_rust) as the primary task/issue tracker for this repo. Trigger when planning work, creating or claiming issues, checking what is ready to work on, or preparing a commit that must reference a beads issue id. |
| `tool-curl` | `model` | any | Use curl for low-level HTTP requests, headers, and data transfer debugging. Trigger when APIs, webhooks, download checks, or protocol-level diagnostics are required. |
| `tool-fd` | `model` | any | Use fd or fd-find for fast filename and path discovery with sane defaults. Trigger when listing or enumerating files and directories by name or glob pattern, without complex find syntax. |
| `tool-fzf` | `user` | any |  |
| `tool-git` | `model` | any | Use git for repository state inspection, safe staging, diff review, and history-aware change management. Trigger when the task involves commits, branches, diffs, or version control decisions. |
| `tool-git-delta` | `user` | any |  |
| `tool-jq` | `model` | any | Use jq to parse, filter, and transform JSON in shell pipelines. Trigger when structured JSON extraction or reshaping is needed. |
| `tool-ripgrep` | `model` | any | Use ripgrep to grep or search a whole repo for text or a regex, extremely fast. Trigger when locating symbols, strings, or patterns at scale. |
| `tool-sd` | `model` | any | Use sd for fast, readable search-and-replace in files with safer defaults than sed. Trigger when swapping one string for another in a batch of files at once. |
| `tool-shellcheck` | `model` | any | Use shellcheck to statically analyze shell scripts for bugs, portability issues, and quoting mistakes. Trigger whenever shell scripts are created or modified. |
| `tool-starship` | `user` | `starship` |  |
| `tool-tmux` | `model` | `tmux` | Use tmux for session orchestration, pane/window control, and resilient long-running terminal workflows. Trigger when the task needs terminal multiplexing or keybinding troubleshooting. |
| `tool-tree` | `model` | any | Use tree to visualize directory layout quickly in a token-efficient form. Trigger when understanding project structure or summarizing file hierarchies. |
| `tool-typos` | `model` | any | Use typos to detect spelling mistakes and misspelled words in code, comments, and docs with low false positives. Trigger when proofreading source content or enforcing text quality. |
| `tool-uv` | `model` | `python` | Use uv for fast Python dependency sync, virtualenv and environment management, and command execution. Trigger for Python setup, install, lint, test, and script workflows in this repo. |
| `tool-wezterm` | `user` | `wezterm` |  |
| `tool-wget` | `model` | any | Use wget for robust non-interactive downloads, retries, and mirror-style fetch operations. Trigger when reliable file retrieval or resumable downloads are needed. |
| `tool-xh` | `model` | any | Use xh as a user-friendly HTTP client for API testing and response inspection. Trigger when quick REST calls with clean defaults are needed. |
| `tool-yq` | `model` | any | Use yq for YAML and structured config queries, edits, and transforms. Trigger when CI configs, manifests, or YAML-based settings need precise changes. |
| `tool-zsh` | `user` | `zsh` |  |
| `worktree-isolation` | `model` | any | Isolate non-trivial work in a sibling git worktree using `basicly worktree`, covering sibling placement on a harness branch, dependency + git-hook provisioning, and safe cleanup. Use when starting a unit of work that should not touch the main checkout, when parallel tracks would collide, or when deciding whether a change needs its own worktree. |
| `wsl` | `model` | `wsl` | Configure and operate WSL (Windows Subsystem for Linux) — wsl.exe management, Windows and Linux interop and PATH gotchas, filesystem layout and performance, and how non-interactive shells differ from login shells. Use when setting up or troubleshooting WSL crossing the Windows and Linux boundary hitting slow /mnt/c file access or debugging a tool that behaves differently in a script than in your terminal. |

<!-- docs-claims:end catalog-skills -->

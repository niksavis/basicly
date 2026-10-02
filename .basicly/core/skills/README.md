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
field: every skill root advertises its first body paragraph instead.

<!-- docs-claims:begin catalog-skills -->

| Skill | Invocation | Technologies | Description |
| --- | --- | --- | --- |
| `best-practices-audit` | `model` | any | Audits skills, instruction files, hooks, agents and permissions against the newest vendor docs. Use after a Claude Code or Codex release. |
| `catalog-authoring` | `model` | any | Authors basicly catalog sources (skills, fragments, styles) in YAML and projects them. Use when adding a new skill or fragment to the catalog. |
| `cli-tools` | `model` | any | Pick the installed command-line tool for a shell task instead of a slower default - rg to search text in files, fd to find or list files by name or extension, bat to read a file with line numbers, jq or yq to read a field from a JSON or YAML file, sd to replace text, ast-grep for code structure, xh or curl to call an HTTP endpoint, just for project recipes. Use for a shell task that searches, lists, reads or reshapes files or data or calls an API; then load tool-NAME for its flags. |
| `conventional-commits` | `model` | any | Writes a commit subject that passes the hooks: type, scope, the breaking-change marker, record id. Use when writing a commit message or a hook refused one. |
| `decompose-plan` | `model` | any | Cuts work into children the plan gate accepts: EARS criteria, scope globs, budgets, a demo command. Use at DECOMPOSE or when a plan gate refuses a child. |
| `falsify-first` | `model` | any | Tries to break a claim, an invariant or a measurement with a concrete counterexample before adopting it. Use before it enters a plan, a design or a gate. |
| `find-skills` | `model` | any | Searches the catalog, then skills.sh, for an existing community skill out there, and installs one only after the user agrees. Use when asking: is there a skill for this? |
| `harness-client` | `model` | any | Attaches to a running basicly supervisor: shows what the factory is doing and records answers to its decisions. Use when a supervisor runs or waits. |
| `harness-loop` | `model` | any | Drives tracked work through the basicly loop from intake to ship. Use when starting or resuming non-trivial work, or advancing an issue past a checkpoint or gate. |
| `interface-facts` | `model` | any | Establishes a third-party CLI flag, API field, model, price or limit from the tool and live vendor docs. Use before code depends on that fact. |
| `no-comments` | `model` | any | Edits code in a repository that bans prose comments: where a fact goes, which directives stay. Use when editing code or when the no-comments gate refuses. |
| `node` | `model` | `node` | Runs Node and npm for the markdownlint hook and other node tools. Use when npm or npx fails or resolves the wrong node binary, often on WSL. |
| `plain-english` | `model` | any | Writes plain prose for readers of any language: READMEs, release notes, design docs, records. Use when writing text that a person reads. |
| `python` | `model` | `python` | Writes Python with type hints, pathlib and cross-platform subprocess calls. Use for .py edits, Windows-only test failures or an except clause for older Python. |
| `python-guidelines` | `model` | `python` | Makes Python design calls no linter checks: where a file splits, whether an abstraction earns its keep, when to silence a warning. Use when a size gate fails. |
| `release-process` | `model` | any | Cuts a basicly release with basicly release, then pushes it and confirms it published. Use when cutting, tagging or checking a release. |
| `repair-in-place` | `model` | any | Fixes a named defect in the lane's own worktree from its findings, with no new plan. Use at REPAIR after verify or validate failed. |
| `retention-probe` | `model` | any | Tests whether this session still holds the always-on instruction file. Use when a repo rule seems missing, in a new subagent or after a compaction. |
| `root-cause` | `model` | any | Finds why a failure happened, with evidence for each why, and names the control that refuses it. Use after a failure, before a fix or a gate. |
| `session-finish` | `model` | any | Closes a session with a usage report, a retro and a handover of what changed and what is open. Use when the user wraps up or a long run ends. |
| `skill-creator` | `model` | any | Writes a new skill or improves one with evals: runs with and without it, a benchmark, graded versions, trigger tuning. Use when turning a workflow into a skill. |
| `test-discipline` | `model` | any | Writes isolated, order-independent tests that assert on what the caller sees, not private helpers. Use for a test with shared fixtures, leaked state or run order. |
| `tier-injection` | `model` | any | Installs the tier kit so a subagent spawn runs on the model its tier names. Use when a host must pin the model of a spawn or a subagent ignores its tier. |
| `tool-ast-grep` | `user` | any | |
| `tool-bat` | `user` | any | |
| `tool-curl` | `user` | any | |
| `tool-direnv` | `user` | any | |
| `tool-fd` | `user` | any | |
| `tool-fzf` | `user` | any | |
| `tool-git` | `user` | any | |
| `tool-git-delta` | `user` | any | |
| `tool-git-lfs` | `user` | any | |
| `tool-jq` | `user` | any | |
| `tool-just` | `user` | any | |
| `tool-lazygit` | `user` | any | |
| `tool-ripgrep` | `user` | any | |
| `tool-sd` | `user` | any | |
| `tool-shellcheck` | `user` | any | |
| `tool-starship` | `user` | `starship` | |
| `tool-tmux` | `user` | `tmux` | |
| `tool-tree` | `user` | any | |
| `tool-typos` | `user` | any | |
| `tool-uv` | `user` | `python` | |
| `tool-wezterm` | `user` | `wezterm` | |
| `tool-wget` | `user` | any | |
| `tool-xh` | `user` | any | |
| `tool-yq` | `user` | any | |
| `tool-zsh` | `user` | `zsh` | |
| `validate-as-consumer` | `model` | any | Runs a verified change the way a consumer does, against its requirement. Use at VALIDATE or before a README or release note claims a capability. |
| `work-tracker` | `model` | any | Reads, files, claims and closes records in the basicly ledger tracker. Use when planning work, checking what is ready, filing a bug or claiming an issue. |
| `worktree-isolation` | `model` | any | Isolates work in a sibling git worktree with basicly worktree, then merges and cleans up. Use to keep a change out of the main checkout or when parallel tracks collide. |
| `wsl` | `model` | `wsl` | Operates WSL: wsl.exe, interop, PATH, slow /mnt/c access. Use when crossing Windows and Linux, or when a tool works in a terminal but not from a script. |

<!-- docs-claims:end catalog-skills -->

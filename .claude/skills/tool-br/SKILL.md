---
name: tool-br
description: Use br (beads_rust) as the primary task/issue tracker for this repo. Trigger when planning work, creating or claiming issues, checking what is ready to work on, or preparing a commit that must reference a beads issue id.
---

# tool-br

## When To Use

- Before starting non-trivial work: check `br ready` for actionable issues, or create one.
- When a task has no existing issue: create one first, then reference its id in the commit.
- When checking what is blocked, in progress, or safe to pick up next.
- When preparing any commit message (a beads issue id is required by the commit-msg hook).

## Trusted Commands

```bash
br init                                   # One-time: initialize .beads/ in a repo
br create "Title" --type task --priority 1 --description "..."
br q "Quick capture title"                # Fast issue creation, id only
br ready                                  # Actionable, unblocked work
br ready --json                           # Machine-readable for agents
br show <id>                              # Issue details
br update <id> --status in_progress --assignee "$(git config user.email)"
br close <id> --reason "What was done"
br dep add <child-id> <parent-id>         # child depends on parent
br list --status open --priority 0-1
br sync --flush-only                      # Idempotent JSONL export check before commit
```

## Safe Defaults

- Always resolve or create an issue before doing the work it represents; never invent
  an id in a commit message that does not exist in `.beads/issues.jsonl`.
- Run `br sync --flush-only` before staging `.beads/` so the JSONL export matches the
  SQLite state (mutating commands auto-flush by default, but this is a cheap final
  check).
- Prefer `br update <id> --status in_progress` before starting work. Close the issue
  with `br close <id> --reason "..."` *before* making the commit that resolves it, then
  stage `.beads/issues.jsonl` together with the code in that same commit — never a
  separate trailing `chore: close <id>` commit with no other content.
- Use `--json` for any programmatic/agent-driven query (`br ready --json`, `br list
--json`, `br show <id> --json`).
- Stage `.beads/issues.jsonl`, `.beads/config.yaml`, and `.beads/metadata.json` with
  the commit that references the issue; `.beads/beads.db*` is git-ignored by br's own
  `.beads/.gitignore`.

## Common Pitfalls

- Running bare `br sync` — it is intentionally refused; choose `--flush-only`,
  `--import-only`, `--merge`, `--status`, or `--witness` explicitly.
- Hand-editing `.beads/issues.jsonl` instead of using `br` commands (breaks the
  SQLite/JSONL sync contract).
- Forgetting the beads id in a commit message — the `beads-commit-msg` git hook
  rejects commits without a known issue id referenced in the message.
- Assuming `br` commits, pushes, or installs hooks automatically — it never does;
  `git add`/`git commit` for `.beads/` files is always the user/agent's responsibility.
- Closing an issue in a separate follow-up commit instead of folding the close into
  the commit that resolves it — creates a no-value trailing commit. If you forgot and
  the resolving commit isn't pushed yet, `git commit --amend` it instead of adding a
  new one; if it's already pushed, amending needs explicit confirmation (history
  rewrite), so weigh that against just accepting the small trailing commit.

## Output Interpretation

- `br ready` lists only issues that are `open` (or configured ready statuses),
  unblocked, and not deferred.
- `br list --json` returns `{"issues": [...], "total": N, "limit": N, "offset": N,
"has_more": bool}`.
- Issue ids follow `<project-prefix>-<short-code>` (this repo's prefix is `basicly`,
  set during `br init`).

## Why It Matters For Agents

- br is the single source of truth for what work exists, what is blocked, and what is
  safe to pick up next — always check `br ready` before assuming there is nothing to
  do or duplicating an existing issue.
- Referencing the issue id in every commit links code history to tracked intent,
  which is enforced mechanically (not just by convention) via the beads-commit-msg
  hook.

## Repo Conventions

- Every commit message must reference at least one valid beads issue id (enforced by
  `.basicly/core/hooks/beads-commit-msg.py`); conventional commit format is still
  required separately (enforced by `.basicly/core/hooks/commit-msg.py`).
- Reference an id as a parenthetical after the conventional commit description, e.g.
  `feat(basicly): add fragment loader (basicly-idr)`.
- Create an issue (`br create` or `br q`) before referencing it; do not fabricate ids.
- Issue prefix is `basicly`; default priority is `2` (Medium) and default type is
  `task` (see `.beads/config.yaml`).
- Priority scale (0=Critical, 4=Backlog): use `0`/`1` sparingly for release-blocking
  or next-up work; `2` for normal work; `3`/`4` for low-urgency/backlog items.
- Type taxonomy is br's canonical enum (`epic`, `feature`, `task`, `bug`, `chore`,
  `docs`, `question`) — there is no separate "story"/"sub-task" type. Express
  hierarchy with `br create --parent <id>` (e.g. epic → feature/task/bug children)
  instead of inventing a new type.

## Trigger Examples

- Should trigger: "What should I work on next in this repo?"
- Should trigger: "Create an issue for the flaky test and start working on it."
- Should trigger: "I'm about to commit — what beads id should I reference?"
- Should not trigger: "Explain how git rebase works."

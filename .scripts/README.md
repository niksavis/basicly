# .scripts

Cross-platform scripts for this repository.

## Conventions

- Prefer Python scripts for portability across Windows, Linux, and macOS.
- Use `uv` to run scripts and tools (for example: `uv run python .scripts/<script>.py`).
- Keep scripts idempotent and non-interactive when intended for CI.
- Avoid hardcoded absolute paths and shell-specific behavior.

## Available scripts

- [`setup_git_identity.py`](setup_git_identity.py) — scaffold per-remote git identities
  via conditional includes, so the right name/email is selected by a repo's remote URL
  without setting a global `user.email`. Carries no identities of its own; pair it with
  the `identity-guard` hook. Run `uv run python .scripts/setup_git_identity.py --help`.
- [`generate_release_changelog.py`](generate_release_changelog.py) — release changelog
  helper (see the release-process skill).

## Git hooks

Git hook scripts moved to [`.basicly/core/hooks/`](../.basicly/core/hooks/) — they
are now a first-class, catalog-distributed artifact type alongside fragments and
skills (see [`docs/architecture.md`](../docs/architecture.md)), not repo-private
scripts. See that directory's README for the hook table and
[`.pre-commit-config.yaml`](../.pre-commit-config.yaml) for how they are wired.

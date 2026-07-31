---
name: release-process
description: Prepare and publish semantic version releases using this repository's changelog-driven workflow. Use this when asked to cut a release, update CHANGELOG.md for a tag, review release notes quality, create/push tags, or verify GitHub release status.
---

# Release Process

Run the repository release flow end-to-end with semantic tags and dated changelog sections.

## Use `basicly release` — do not hand-run the preparation steps

Everything up to and including the annotated tag is automated (`basicly release`,
component 9). Run it instead of typing the steps below:

```sh
basicly release 0.6.0 --issue <beads-id> --dry-run   # pre-flight; writes nothing
basicly release 0.6.0 --issue <beads-id>             # bump, regenerate, pins, changelog, commit, tag
```

It bumps the single-sourced `__version__`, regenerates the version-stamped
projections, rewrites the `@vX.Y.Z` pins in `README.md`, `site/index.html` **and both
bootstrap shims**, upserts the dated `CHANGELOG.md` section, commits, and creates the
annotated tag. It **never pushes**.

## Curate the changelog *before* you run it — under `## [Unreleased]`

The run promotes the `## [Unreleased]` body into the dated `## vX.Y.Z - <date>`
section and leaves `[Unreleased]` empty; the commit-subject dump is only the
fallback for a release nobody wrote notes for. So write the prose first, under
`[Unreleased]`, in the `### Added` / `### Fixed` shape the older sections use.

Curating afterwards does not work and is the trap this replaced (`basicly-m3od.1`):
the commit and the annotated tag are one step, and `.github/workflows/release.yml`
reads `CHANGELOG.md` from the **tagged** commit — so anything added in a later
commit never reaches the published release body. A re-run keeps an already-curated
section rather than overwriting it with the dump.

Two things it deliberately does not do, which stay with you:

- **Push.** Publishing is irreversible; run the two `git push` commands it prints.
- **Decide the version.** Pass it explicitly; the command refuses one that does not
  move forward.

It refuses before writing anything on a dirty tree, a non-forward version, an
existing tag, a bad date, an unknown `--issue`, a commit subject the `commit-msg`
gate would reject, or a linked worktree (tags are shared with the primary
checkout). A failure after the first write restores the tree, so a half-released
repo needs no `git reset --hard`.

`--autonomous --root <epic>` is the delegated form and needs an **L3** grant inside
its spend ceiling with green lights-out preconditions; pass `--shipping <node>` to
name the node whose gates are checked, because an open epic's own verify gate is
never green.

The manual workflow below remains the reference for what the command automates and
for the publication half.

## Scope

This skill handles release preparation and publication for this repository.

It is not for:

- Editing unrelated product features.
- Rewriting git history.
- Skipping quality gates.

## Inputs

- Target semantic tag (for example `v0.1.1`).
- Release date in ISO format (`YYYY-MM-DD`).
- Optional manual edits to changelog highlights before tagging.

## Outputs

- Updated `CHANGELOG.md` section for the target tag.
- Tag pushed to origin.
- GitHub release created by workflow with notes sourced from changelog.
- Verification summary with run URL and release URL.

## Workflow

1. Verify branch state and quality gates.

- Confirm working tree is clean.
- Confirm required checks pass locally (`pre-commit`, tests) when relevant.

1. Ensure release changes are committed first.

- Commit any release workflow/tooling updates before generating changelog for the new tag.

1. Generate changelog section.

- Run:
- `uv run python .scripts/generate_release_changelog.py --tag vX.Y.Z --date YYYY-MM-DD`
- This computes commit delta from previous semantic tag to `HEAD`.

1. Review changelog text for end-user clarity.

- Keep a concise `### Highlights` section.
- Keep `### Commit delta (auto-generated)` for traceability.
- Ensure heading format is exact: `## vX.Y.Z - YYYY-MM-DD`.

1. Commit changelog update.

- `git add CHANGELOG.md`
- `git commit -m "docs(release): update changelog for <release>"`

1. Push main branch.

- `git push origin main`

1. Create annotated semantic tag with date.

- `git tag -a vX.Y.Z -m "vX.Y.Z (YYYY-MM-DD)"`
- `git push origin vX.Y.Z`

1. Verify release publication.

- Check release workflow run status.
- Confirm GitHub release body matches the tag section from `CHANGELOG.md` plus the pinned `uvx` install line.

## Guardrails

- Never force-push or rewrite history for release flow unless explicitly requested.
- Never tag from a dirty working tree.
- Never skip CI/quality gate failures.
- Do not include user-specific local paths or secrets in changelog/release notes.

## Trigger Examples

Should trigger:

- "Cut v0.1.2 release and publish notes."
- "Generate release notes from changelog and tag."
- "Prepare next semantic release with date and push tag."

Should not trigger:

- "Fix this installer bug in prerequisites.py."
- "Refactor tmux config keybindings."
- "Explain how CHANGELOG works conceptually."

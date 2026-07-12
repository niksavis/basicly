# Releases

Maintainer notes for publishing tagged releases.

## Release workflow

- File: `.github/workflows/release.yml`
- Triggered on semantic tag push (`v*`).
- Uses the matching section in `CHANGELOG.md` as release notes source.
- Requires section heading format: `## vX.Y.Z - YYYY-MM-DD`.

## Optional install notes

- The release workflow publishes changelog text as-is.
- If you want an install snippet in release notes, add it directly to the matching
  `CHANGELOG.md` section for that tag.
- Keep install guidance repository-generic (avoid hardcoding old project names).

## Maintainer steps

1. Ensure `main` is green and all release code changes are committed.
1. Generate/update changelog for the target semantic tag and date with `uv run python .scripts/generate_release_changelog.py --tag v0.1.0 --date 2026-07-12`.
1. Review `CHANGELOG.md` and edit text for end-user clarity when needed.
1. Commit changelog updates with `git add CHANGELOG.md && git commit -m "docs(release): update changelog for v0.1.0"`.
1. Push `main` with `git push origin main`.
1. Create an annotated semantic version tag with the release date in the message using `git tag -a v0.1.0 -m "v0.1.0 (2026-07-12)"`.
1. Push the tag with `git push origin v0.1.0`.
1. Review the generated GitHub release page and verify notes were copied from `CHANGELOG.md`.

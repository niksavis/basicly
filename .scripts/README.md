# .scripts

Cross-platform scripts for this repository.

## Conventions

- Prefer Python scripts for portability across Windows, Linux, and macOS.
- Use `uv` to run scripts and tools (for example: `uv run python .scripts/<script>.py`).
- Keep scripts idempotent and non-interactive when intended for CI.
- Avoid hardcoded absolute paths and shell-specific behavior.

## Available scripts

- `python src/basicly/lefthook_artifacts.py --download` prepares the pinned,
  checksum-verified Lefthook release for source development and packaging.
  Use `--from-dir <directory>` for supplied local artifacts. Assets stay in
  ignored `build/lefthook/`; builds and consumer installs never download them.
  Every entry names the official upstream release URL, so the repository holds no
  binary, archive or patch.
  To bump Lefthook, update `src/basicly/lefthook_assets/manifest.json` with the
  chosen version, official release URLs and the SHA-256 hashes that upstream
  publishes in `lefthook_checksums.txt`, for all six binaries and the checksum
  file. Review the new license, prepare with
  `--download`, and test the built distributions before releasing basicly.
  The manifest also sets the generated configuration's minimum version.
  Consumers receive and replace the old executable when they rerun install with
  that basicly release; their Lefthook version follows basicly's pin.
- [`setup_git_identity.py`](setup_git_identity.py) — scaffold per-remote git identities
  via conditional includes, so the right name/email is selected by a repo's remote URL
  without setting a global `user.email`. Carries no identities of its own; pair it with
  the `identity-guard` hook. Run `uv run python .scripts/setup_git_identity.py --help`.
- [`generate_release_changelog.py`](generate_release_changelog.py) — release changelog
  helper (see the release-process skill).
- [`docs_claims.py`](docs_claims.py) — generate the documentation blocks this repo can
  derive from its own tree (always-on character counts, catalog inventories) and assert
  the claims it can only check (every shipped subcommand appears in the architecture
  command tables). Wired as the `docs-claims` [`[[verify.checks]]`](../basicly.toml)
  entry, so `--check` gates every commit and `--fix` is the mechanical repair.
- [`headroom.py`](headroom.py) — report how many tokens a module may still add before
  `module-size` refuses it, against its cap or its frozen baseline. Not a gate — the ratchet
  already binds at commit time; this is the read that sizes a change before it is written. Run
  `uv run python .scripts/headroom.py <path>`, or with no path for every module an ordinary
  edit would take past a bound.
- [`generate_model_map.py`](generate_model_map.py) — resolve each model tier's anchor
  against models.dev into the committed [`model-map.json`](../.basicly/core/models/README.md),
  and `--check` it for upstream drift. Needs the network, so it runs at authoring and
  check time only — never in the dispatch path and not as a commit-time gate.

## Native Claude permission probe

On POSIX with Claude Code installed, run `uv run python .scripts/probe_claude_permissions.py --group <group>` for `git_options`, `remote_deletes`, `readonly_allow` and `tracker_allow`. Each group reads `tests/fixtures/permissions_native.json` and the catalog, uses a local Messages API fixture to supply exact Bash calls, and substitutes harmless Git, GitHub and basicly executables. No real destructive command or vendor API request runs. JSON reports and transcripts stay in the printed temporary directory.

Deny groups grant their command families so a missing deny fails; allow groups grant only the catalog. Each has positive and negative controls. `--catalog <permissions-directory>` reruns the same matrix against a previous catalog. The probe uses native `dontAsk` permission matching, not an imitation glob matcher or an auto-mode classifier. Validate classifier policy separately with the real service; fixture API usage counters are synthetic. Recheck after Claude upgrades.

## Git hooks

Git hook scripts moved to [`.basicly/core/hooks/`](../.basicly/core/hooks/) — they
are now a first-class, catalog-distributed artifact type alongside fragments and
skills (see [`docs/architecture/architecture.md`](../docs/architecture/architecture.md)), not repo-private
scripts. See that directory's README for the hook table and
[`.basicly/lefthook.yml`](../.basicly/lefthook.yml) for how they are wired.

# File Audit

A one-time sweep (basicly-vk1n) for obsolete/removable files and for tracked
files whose purpose is non-obvious. Method: tracked set from `git ls-files`,
untracked/ignored from `git status --porcelain --ignored`, reference checks with
`git grep`/`rg` across `src/`, `docs/`, `*.md`, `.github/`, `.basicly/`,
`.scripts/`, `pyproject.toml`, `basicly.toml`, and the static site.

No files are deleted by this audit. Confirmed candidates are tracked as
follow-up beads (see the bottom of each entry); deletions and dependency changes
happen there, under explicit confirmation.

## Removal candidates

### `.basicly/core/fragments/code-style/` — safe to remove

An empty, untracked local directory. Git cannot track an empty dir, so it is
absent from a fresh clone; it is a scaffold for the valid `code-style` catalog
category that never received a fragment.

Checks:

- `git ls-files` and `git log --all` for the path → empty (never tracked).
- `git check-ignore` → not ignored.
- `rg "code-style"` → 13 hits in 6 files, all the taxonomy *category* name
  (`schema.py`, `architecture.md`, fixtures/tests), none the directory path.

Resolution: `rmdir` the empty dir, or author a `code-style` fragment there if
one is intended. Nothing currently requires it. Follow-up: **basicly-a1it**.

### `docs/foundry-spike.md` — needs human decision (lean: archive, not delete)

The research-spike deliverable for `basicly-zv48`. The spike is complete — all
seven dimension children and every implementation follow-on are closed/shipped;
only the two documentation beads (`basicly-uv05`, `basicly-vk1n`) remain.

Checks:

- `rg "foundry-spike"` (path/filename) → 0 hits; not linked from
  `docs/index.html` or any nav; no site generator exists, so it is in no build.
- `rg -i "foundry"` (excluding the file) → 2 hits, both design-provenance
  comments citing "foundry spike Dimension N": `src/basicly/permissions.py:6`
  and `src/basicly/rubrics.py:6`.

Why not a clean delete: those two source comments cite dimensions this doc is
the only place to define. Nothing breaks if removed, but the design record is
lost. Recommend archiving (move under `docs/archive/` or add a "superseded —
spike complete" banner) rather than deleting. Follow-up: **basicly-9ugx**.

### `.curlylint.yaml` (+ `curlylint` dev dep) — needs human decision (dead config)

Jinja-template linter config that is never invoked.

Checks:

- `git grep -i "curlylint"` → only `.curlylint.yaml` itself and the
  `pyproject.toml` dev-dependency line. Not in `.pre-commit-config.yaml`, any
  `.github/workflows/`, or `basicly.toml [[verify.checks]]`.
- Doubly stale: `include: templates` points at a nonexistent top-level
  `templates/`; the real templates live at `.basicly/core/templates/*.j2`.

Resolution: either wire curlylint into a gate pointed at
`.basicly/core/templates`, or remove `.curlylint.yaml` and drop the dev
dependency (dependency removal is confirmation-gated). Follow-up: **basicly-1x1u**.

### `.importlinter` (+ `import-linter` dev dep) — needs human decision (unwired config)

An `engine-independence` import contract (forbids `basicly` importing
`basicly.fragments`/`basicly.targets`) that is never enforced.

Checks:

- `git grep -i "import.linter|lint-imports|importlinter"` → only `.importlinter`
  and the `pyproject.toml` dev-dependency line. `lint-imports` runs in no hook,
  CI step, or `verify.checks`.
- The contract is architecturally meaningful — this reads as intended-but-unwired
  governance, not pure cruft.

Resolution: wire `lint-imports` into `[[verify.checks]]`/CI, or remove the
config and dev dependency (confirmation-gated). Follow-up: **basicly-j9w4**.

### `docs/.nojekyll` — keep

A zero-byte GitHub Pages marker that disables Jekyll so the hand-built `/docs`
static site is served as-is. No Pages deploy workflow exists, so Pages (if
enabled) serves `docs/` directly in branch-folder mode, which runs Jekyll by
default; the marker is the standard way to stop that. Harmless and conventional.
Documented in the inventory below.

### Untracked/ignored cruft — no action

`git status --porcelain --ignored` shows only properly ignored artifacts
(`.venv/`, `dist/`, `node_modules/`, `__pycache__/`, caches, `.doctor/`,
`.basicly/usage/`, beads runtime files, `.claude/settings.local.json`). No
tracked `.bak`/`.tmp`/`.orig`/`.swp`/`.old` files exist. Everything in
`.scripts/` is referenced by README/docs/CHANGELOG and covered by tests.

## Inventory of non-obvious tracked files

Load-bearing unless noted.

| Path | Purpose | Load-bearing? |
|---|---|---|
| `docs/.nojekyll` | GitHub Pages marker disabling Jekyll for the hand-built `/docs` static site | Yes |
| `basicly.toml` | This project's own basicly config: catalog `[paths]`, `[catalog]` technologies, `[[verify.checks]]` (the ruff/pyright/bandit/pytest gate commands the git hooks run), `[policy]` loop gates | Yes — drives the git-hook check runner |
| `.basicly/generated-manifest.json` | Generated projection manifest tracking what basicly wrote (idempotent regen / protect-generated backstop) | Yes (generated; do not hand-edit) |
| `.github/hooks/basicly-tool-usage-copilot.json` | Copilot-target hook config running `tool-usage.py` on `postToolUse` | Yes |
| `.beads/config.yaml`, `.beads/metadata.json` | beads tracker config; `metadata.json` maps `beads.db` ↔ `issues.jsonl` | Yes |
| `.beads/issues.jsonl` | Canonical exported tracker state (committed source of truth; `.db` is ignored) | Yes |
| `.gitattributes` | Forces LF on checkout + text/binary classification (cross-platform hygiene) | Yes |
| `.python-version` | Pins the Python interpreter floor (3.14+) for `uv` | Yes |
| `.curlylint.yaml` | Jinja-template linter config — never invoked; `include` points at a missing dir | No (dead — see above) |
| `.importlinter` | import-linter `engine-independence` contract — `lint-imports` never invoked | No (unwired — see above) |
| `.ruff.toml` | Ruff lint/format config | Yes |
| `.markdownlint-cli2.yaml` | markdownlint-cli2 config (the node-based markdown git hook) | Yes |
| `.pre-commit-config.yaml` | Wires the git hooks via `uv run python .basicly/core/hooks/*.py` + markdownlint | Yes |
| `package.json`, `package-lock.json` | Node presence purely for the `markdownlint-cli2` devDependency (markdown hook) | Yes |
| `.agents/skills/**` | Second projected skill root (Agent Skills open standard) alongside `.claude/skills/`; both generated by `skills-build` | Yes (generated) |
| `AGENTS.md`, `.github/copilot-instructions.md` | Projected agent-instruction outputs for the codex/copilot targets | Yes (generated) |
| `.vscode/*.json` | Editor config (extensions, launch, tasks, settings) | No (dev convenience, not a gate) |

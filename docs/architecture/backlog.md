# Architecture backlog — a holding pen, not a home

**Every entry here belongs in the work tracker.** A defect found while writing
[`architecture.md`](architecture.md) and filed nowhere spends the reader's attention for
free. A defect carried as a document section is filed nowhere; it only looks filed.

This file exists so that moving the backlog out of the architecture document lost nothing.
Each entry should become a bead, and the entry should then be deleted rather than kept in
step. Nothing gates that.

Entries are in dependency order. Each is shaped for the plan gate: EARS acceptance
criteria, scope globs, declared dependencies, an integrity level, and a runnable
demonstration.

---

## B1 — Rename the two ladders in code

**Why.** Architecture §8 gives every autonomy and integrity level a name that says what it
means. The code still writes `L0` to `L3` for autonomy and `L1` to `L3` for integrity
(`integrity.LEVELS`). Two names for one thing is the defect the architecture document exists
to catch, and it now holds one deliberately.

| Item | Value |
| --- | --- |
| Scope | `src/basicly/integrity.py`, `src/basicly/config.py`, `src/basicly/policy.py`, `src/basicly/cli.py`, `basicly.toml`, `.basicly/core/schemas/**`, `docs/**` |
| Integrity | `consumer-surface`. `basicly.toml` and the CLI flag values are both frozen surfaces |
| Depends on | nothing |
| Acceptance | WHEN a classification is recorded, THE ENGINE SHALL write the new name. WHEN a `basicly.toml` carries an old level value, THE ENGINE SHALL accept it and name the new spelling in a deprecation message. WHEN `--autonomy` is given an old value, THE CLI SHALL accept it for one minor release |
| Demonstrated by | `uv run basicly policy grant --show --root <id>` printing a named level, and `uv run pytest tests/test_integrity.py tests/test_policy_grants.py -q` |
| Cost | a frozen surface changes, so it needs a deprecation window and a changelog fragment |
| Buys | one name per concept, and a level a reader understands without a lookup table |

---

## B2 — Correct every `architecture §N` citation in code, and gate the direction

**Why.** Architecture §2.6 states that section numbers are a cited surface. The rewritten
document renumbered every section, so every existing citation in the tree is stale.
`.scripts/check_docs_citations.py` walks `docs/**/*.md` for `file:line` references into
code. Nothing walks code for `§N` references into a document, which is why the stale
citations sat in the tree with every gate green.

**This must land in the same change that swaps `architecture-v2.md` over
`architecture.md`.** A renumbered document with uncorrected citations is worse than a
document with no numbers: a citation that resolves to the *wrong* section reads as correct.

The mapping is mechanical and is recorded in the rewrite changelog.

| Item | Value |
| --- | --- |
| Scope | `docs/architecture/architecture.md`, `src/basicly/*.py`, `tests/test_skill_source.py`, `.basicly/README.md`, `.basicly/core/hooks/**`, `.basicly/core/targets/codex.yaml`, `docs/requirements/factory-loop.md`, `.scripts/check_docs_citations.py`, `basicly.toml` |
| Integrity | `engine` |
| Depends on | the architecture rewrite landing |
| Acceptance | WHEN code cites the architecture document, IT SHALL cite a section number the document currently defines. WHEN a cited number names no heading, THE CHECK SHALL exit non-zero and name the file, the line and the missing number. WHEN every citation resolves, THE CHECK SHALL exit zero and print the citation count |
| Demonstrated by | the new check reporting at least 25 citations and 0 failures on the fixed tree, and exactly 1 failure after deleting one heading; plus `uv run pytest tests/test_docs_citations.py -q` |
| Cost | 25 comment and prose edits in a repository whose comment-density ratchet is at its cap. Measure per-file headroom before editing |
| Buys | the code can cite the design again, and a citation cannot go stale in silence |

---

## B3 — Validate every mermaid block

**Why.** `architecture.md` carries 16 mermaid blocks after the rewrite, and the README
carries 1. **Nothing checks that any of them parses** [measured 2026-08-16:
`rg -i mermaid` over `.scripts/`, `src/` and `.pre-commit-config.yaml` returns nothing,
against a positive control that returns matches for `basicly` in the same files]. A block
with a syntax error renders as a red error box on the hosting site, and no gate here would
stop it landing.

The defect is not hypothetical. One revision of the architecture document named a
`sequenceDiagram` participant `Loop`, which collides with mermaid's `loop` keyword. A parser
caught it. Review did not.

| Item | Value |
| --- | --- |
| Scope | `.scripts/`, `package.json`, `.pre-commit-config.yaml`, `basicly.toml` |
| Integrity | `engine` |
| Depends on | nothing |
| Acceptance | WHEN a tracked markdown file holds an unparseable mermaid block, THE CHECK SHALL exit non-zero and name the file, the line and the parser message. WHEN every block parses, THE CHECK SHALL exit zero and print the block count |
| Demonstrated by | a check that reports the tree's current block count and 0 failures, and 1 failure after a deliberate typo |
| Cost | **a dependency addition**, and therefore a human decision. It needs node plus `mermaid` and `jsdom`. `@mermaid-js/mermaid-cli` is not the answer — it declares a `puppeteer` peer dependency, which means a browser download |
| Buys | the only defect class in the architecture document that is invisible to every existing gate and visible to every reader |

**A correction carried forward from the previous version of this item.** Its cost paragraph
quoted "102 packages, 181 MB, and 426 ms to validate all blocks in two files, measured on
this machine". `node_modules/@mermaid-js/parser` is present on the authoring machine while
`package.json` declares only `markdownlint-cli2`, so that measurement came from an untracked
install nobody can reproduce. The figure is unverifiable from the repository, and the number
a human is being asked to approve is therefore unknown. Re-measure before approving.

Its acceptance also once said "reports 12 blocks", which counted the legend as a view. The
count belongs in the check's output, not in the acceptance criterion.

---

## B4 — The install fact is missing from the consumer surfaces

**Why.** Architecture §20 states that `install` and `uninstall` are ordinary verbs, and that
`uvx` is only how you reach an executable your machine does not have. **The README, both
how-to pages and the tutorial teach only the `uvx` form** [measured 2026-08-16,
`rg 'uvx|basicly install' README.md docs/how-to/ docs/tutorial/`]. A reader concludes the
long form is the command.

| Item | Value |
| --- | --- |
| Scope | `README.md`, `docs/how-to/upgrade-and-check-drift.md`, `docs/how-to/customize-the-catalog.md`, `docs/tutorial/first-loop.md`, `site/index.html` |
| Integrity | `docs-and-tests` |
| Depends on | nothing |
| Acceptance | WHEN a consumer reads the install section of any of those surfaces, IT SHALL state that `uvx --from ...` is one of three ways to reach the same verb, and that `basicly install` needs the executable on `PATH` first |
| Demonstrated by | `uv run python .scripts/docs_claims.py --check` staying green, and `uv run pytest tests/test_docs_claims.py -q` |
| Cost | five files of prose |
| Buys | it removes the reading that `uvx` is part of the command's name |

**Note on the previous acceptance.** It read "`rg -c 'on PATH' README.md` returning at least
1". That already returns 1 today, from the `br` prerequisite sentence, so half the acceptance
was satisfied before the work started. The demonstration above replaces it.

---

## B5 — Default both skill roots

**Why.** Architecture §13 marks this as a target. `skills.resolve_skill_roots` writes
`DEFAULT_SKILL_ROOTS[0]` alone unless the caller passes `--all-default-roots` or an explicit
`--root`. `basicly install` passes it and is correct; a bare `basicly skills-check` is not.
This repository's own `CLAUDE.md` compensates with guidance, which is a prose gate standing
in for a default. The agent roots already do the right thing: `agents-build` takes no root
flag and always writes both.

| Item | Value |
| --- | --- |
| Scope | `src/basicly/skills.py`, `src/basicly/cli.py`, `tests/test_skills.py`, `.basicly/core/fragments/**` |
| Integrity | `consumer-surface`. The CLI flag set is a frozen surface |
| Depends on | nothing |
| Acceptance | WHEN `basicly skills-build` runs with no root flag, IT SHALL write every default root. WHEN `basicly skills-check` runs with no root flag, IT SHALL check every default root. WHEN `--root` names a root explicitly, ONLY that root SHALL be written |
| Demonstrated by | `uv run basicly skills-check` exiting zero and naming both roots, and `uv run pytest tests/test_skills.py -q` |
| Cost | `--all-default-roots` becomes a no-op and needs a deprecation note |
| Buys | the guidance that compensates for the default can be deleted, and a second root cannot drift unnoticed |

---

## B6 — Say "pin" in `CONTRIBUTING.md`

**Why.** Architecture §31.7 records that the README, both how-to pages and the tutorial now
say "a pin, not a floor". `CONTRIBUTING.md:37` still calls `0.2.16` "the known-good floor",
and the code warns in both directions from an exact pin.

| Item | Value |
| --- | --- |
| Scope | `CONTRIBUTING.md` |
| Integrity | `docs-and-tests` |
| Depends on | nothing |
| Acceptance | WHEN a contributor reads the tracker-binary prerequisite, IT SHALL say the version is an exact pin and that the harness warns on any other version in either direction |
| Demonstrated by | `rg -c 'known-good floor' CONTRIBUTING.md` returning 0, against a positive control of `rg -c '0.2.16' CONTRIBUTING.md` returning at least 1 |
| Cost | one sentence |
| Buys | the last surviving instance of a contradiction the architecture document used to record rather than resolve |

---

## B7 — Move the CLI reference out of the architecture document

**Why.** Architecture §21 is marked as a target and says so in its first line. It is a
per-command behaviour table in a specification, it is the largest single section, and it
goes stale on every landing. It stays only because four gates bind on it.

| Item | Value |
| --- | --- |
| Scope | `docs/architecture/architecture.md`, `docs/reference/cli.md`, `.scripts/docs_claims.py`, `tests/test_docs_drift.py`, `basicly.toml` |
| Integrity | `engine`. The assertion targets move |
| Depends on | B2 |
| Acceptance | WHEN the CLI ships a subcommand, THE `cli-commands` AND `cli-subcommands` ASSERTIONS SHALL check the CLI reference and not the architecture document. WHEN a subcommand is removed, THE REVERSE TRIPWIRE SHALL check the CLI reference. WHEN the architecture document is read, IT SHALL NOT contain a per-command behaviour table |
| Demonstrated by | `uv run python .scripts/docs_claims.py --check` green after the target move, plus `uv run pytest tests/test_docs_drift.py -q` |
| Cost | a new document, four gate retargets, and one more file in the documentation set |
| Buys | the architecture document stops carrying a class of fact that goes stale on every landing |

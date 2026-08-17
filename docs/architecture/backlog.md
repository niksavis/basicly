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

**An entry label is stable and is never renumbered**, for the same reason an architecture
section number is not: other documents cite it. A later item that must run *before* an
existing one takes a decimal under it and sits in its dependency position, so `B8.1` precedes
`B8` in the file. Read the `Depends on` row, not the ordinal.

---

## B1 — Rename the two ladders in code

**Why.** Architecture §9 gives every autonomy and integrity level a name that says what it
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

**Why.** Architecture §3 states that section numbers are a cited surface. The rewritten
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
| Scope | `docs/architecture/architecture.md`, `src/basicly/*.py`, `tests/test_skill_source.py`, `tests/test_docs_drift.py`, `.basicly/README.md`, `.basicly/core/hooks/**`, `.basicly/core/targets/codex.yaml`, `docs/requirements/factory-loop.md`, `.scripts/check_docs_citations.py`, `.scripts/docs_claims.py`, `basicly.toml` |
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
| Acceptance | WHEN a tracked markdown file holds a mermaid block the renderer refuses, THE CHECK SHALL exit non-zero and name the file, the line, the renderer version and its message. WHEN every block renders, THE CHECK SHALL exit zero and print the block count and the renderer version |
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

**A second correction, and it is the one that decides whether this item is worth
building.** The acceptance above used to say *parses*. A parse is the wrong instrument.
Measured 2026-08-16 against mermaid 11 in a real browser: `mermaid.parse()` **accepts** a
`stateDiagram-v2` block whose transition label carries a second colon, and a renderer
**refuses** the same block with *"No diagram type detected matching given
configuration"*. A gate written to the old criterion would have passed the exact block a
reader reported as a red error box. The criterion now says *renders*.

**One thing is unestablished and blocks sizing.** The reported failure could not be
reproduced on mermaid 11 — that build renders the offending block. The reporting renderer
is therefore a different version or configuration, and it is unknown which. **Establish
which renderer the hosting surface uses, and its version, before approving the
dependency**: a check pinned to the wrong renderer is a gate that agrees with itself.

---

## B4 — The install fact is missing from the consumer surfaces

**Why.** Architecture §21 states that `install` and `uninstall` are ordinary verbs, and that
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

**Why.** Architecture §14 marks this as a target. `skills.resolve_skill_roots` writes
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

**Why.** Architecture §37.5 records that the README, both how-to pages and the tutorial now
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

**Why.** Architecture §22 is marked as a target and says so in its first line. It is a
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

---

## B8.1 — Give the closed event-kind set one definition

**Why.** The vocabulary is declared in four modules and they disagree about whether a live
kind is known. `events.py` declares six `KIND_*` constants; `migrate.py` declares
`KIND_EDGE`; `gates.py` and `differential.py` each declare `KIND_GATE`, a duplication
`gates.py` documents at its own top. `events.KNOWN_KINDS` is the name a reader reaches for
when they want the closed set: it is missing two of the six kinds actually in the log, and it
has **no consumer anywhere, including the suite**
[measured 2026-08-17, `git grep -n KNOWN_KINDS -- '*.py'` returns its definition and nothing
else, against a positive control of `KIND_COMMENT` which returns six files].

**This is first because B8 adds five kinds.** Adding five entries to a vocabulary with four
partial definitions is how the sixth and seventh spellings appear. Architecture §32.8 carries
the table. The `KIND_GATE` duplication is already owned by `basicly-vkh0.27`; this item is the
single definition the rest of the kinds need, not a re-file of that one.

| Item | Value |
| --- | --- |
| Scope | `.basicly/core/kit/tracker/events.py`, `.basicly/core/kit/tracker/migrate.py`, `.basicly/core/kit/tracker/gates.py`, `tests/test_kit_tracker_events.py` |
| Integrity | `engine`. No consumer surface carries a kind constant |
| Depends on | nothing |
| Acceptance | WHEN a module needs the closed set of event kinds, IT SHALL read one declaration in `events.py`. WHEN a kind is folded by a sibling module rather than by `events.fold`, THE CLOSED SET SHALL still contain it. WHEN the closed set omits a kind present in a log the suite folds, THE SUITE SHALL fail and name the kind. WHERE a second module declares a kind constant, IT SHALL import it rather than spell it |
| Demonstrated by | `uv run pytest tests/test_kit_tracker_events.py -q`, plus `uv run python -c "import importlib.util,sys;from pathlib import Path;s=importlib.util.spec_from_file_location('ev',Path('.basicly/core/kit/tracker/events.py'));m=importlib.util.module_from_spec(s);sys.modules['ev']=m;s.loader.exec_module(m);print(sorted(m.KNOWN_KINDS))"` listing every kind the ledger holds |
| Cost | one constant gains consumers, which means it gains a contract |
| Buys | the closed set becomes checkable, so B8's five additions can be refused if they diverge |

---

## B8.2 — Separate a delegated kind from an unknown one in the fold

**Why.** `events.fold` reports `FoldResult.unknown_kinds` for any kind it has no handler for.
Folding this repository's log reports `{'edge': 951, 'gate': 8}` — **959 events, 17.9% of the
log** — and neither is unknown [measured 2026-08-17 at `fb19039`, `events.read_log` then
`events.fold`, reading `FoldResult.unknown_kinds`]. Both are deliberately folded by a sibling
module, and `fsck._unfolded_kind_findings` admits the ambiguity in its own warning text:
"either a newer writer's, or one a sibling module derives from the events directly".

**This is second because it is B8's safety net.** D-34's stated catastrophe is a `comment`
event taking the skip path and silently dropping checkpoint and gate state. The signal that
would catch it currently cannot tell a deliberate delegation from an unreadable event, and B8
adds five more delegating kinds to the same bucket. A net with 959 false entries in it does
not catch the 960th.

| Item | Value |
| --- | --- |
| Scope | `.basicly/core/kit/tracker/events.py`, `.basicly/core/kit/tracker/fsck.py`, `tests/test_kit_tracker_events.py`, `tests/test_kit_tracker_fsck.py` |
| Integrity | `engine`. `fsck` exit codes are a surface; the finding set is not |
| Depends on | B8.1 |
| Acceptance | WHEN the fold reads a kind a sibling module folds, IT SHALL report it as delegated and SHALL NOT report it as unknown. WHEN the fold reads a kind no module folds, IT SHALL report it as unknown. WHEN `fsck` runs on a log holding only delegated kinds, IT SHALL emit no unfolded-kind warning. WHEN `fsck` runs on a log holding a genuinely unrecognised kind, IT SHALL warn and name it |
| Demonstrated by | `uv run pytest tests/test_kit_tracker_events.py tests/test_kit_tracker_fsck.py -q`, plus `uv run python .basicly/core/kit/tracker/fsck.py .basicly/ledger` reporting no unfolded-kind warning for `edge` or `gate` |
| Cost | one field on `FoldResult` becomes two, and `fsck`'s finding set changes shape |
| Buys | the skip-path refusal B8 depends on becomes a signal instead of noise |

---

## B8.3 — Bind the marker-family list to a gate

**Why.** The alias table B8 needs has the **marker family list as its domain**, and nothing
binds that list. It has now drifted three times.
[`docs/requirements/work-tracker.md`](../requirements/work-tracker.md) records the first two —
a count that read eight while four families had shipped, then a correction to ten that was
itself wrong — and the list standing at twelve is wrong in **both** directions. It names
`[harness-side]`, which is not a marker family but a phrase from a sentence in
`src/basicly/commit.py` reading "the rescue is harness-side because it has to be", and it
omits `[harness-retro]`, declared in `src/basicly/retrospective.py`. The count from the
declarations is **eleven**
[measured 2026-08-17, `git grep -ohn '"\[harness-[a-z-]*\]"' -- 'src/basicly/*.py' | sort -u`].

**The list is not the same set as the alias table's domain, and that is the trap.**
`[harness-overrun]` carries **12 rows** in the ledger and has no producer in the tree: the
string survives only in two *negative* assertions, `tests/test_loop.py` and
`tests/test_supervise.py`, each asserting the marker is never written. So a table derived from
the live declarations omits it and loses those rows. The gate must therefore count two
populations and compare them: the families declared in code, and the families present in the
log.

**This is the same defect class as a dead-code gate that counted English prose in a schema as
a field reference.** A list of wire formats counted by eye is the instrument fault.

| Item | Value |
| --- | --- |
| Scope | `.scripts/check_marker_families.py`, `basicly.toml`, `docs/requirements/work-tracker.md`, `tests/test_marker_families.py` |
| Integrity | `engine`. A new advisory-then-blocking check |
| Depends on | nothing |
| Acceptance | WHEN the check runs, IT SHALL derive the declared family set from the marker constants in `src/basicly/` and SHALL NOT count a family named only in prose. WHEN a family appears in the ledger but not in the declarations, THE CHECK SHALL report it as retired and SHALL NOT treat it as absent. WHEN a declared family is missing from the frozen list, THE CHECK SHALL fail and name it. WHERE a document states a family count, THE CHECK SHALL compare that count against the derived set |
| Demonstrated by | `uv run python .scripts/check_marker_families.py` reporting eleven declared and one retired, and exiting non-zero when a marker constant is added without updating the frozen list, plus `uv run pytest tests/test_marker_families.py -q` |
| Cost | one more check in the verify set, and a frozen list to maintain |
| Buys | B8's alias table gets a checked domain instead of a hand-counted one, so a retired family cannot silently lose its rows |

---

## B8 — Split the event vocabulary: `note` for prose, typed kinds for machine state

**Why.** Architecture §32.3 and **D-34** specify it. Filed upstream as `basicly-vkh0.30`.
Measured on this repository's ledger [2026-08-17 at `fb19039`, the census command in
architecture §32.3]: **2,540 of 5,353 events are `comment`** (47.4%), holding 2,216,283 of the
log's 5,300,416 bytes, and carrying human prose *and* checkpoints, gate results, handoff
artifacts, decision items, scope violations, telemetry and worktree bindings. The `gate` kind,
built for gate verdicts, holds **8**. The log grows on every session, so re-run the census
rather than quoting these figures.

**The target set is eighteen kinds, and the partition decided that.** Routing the 2,540
`comment` rows through the thirteen kinds §32.3 first listed leaves **585 rows, 23%**, with
nowhere to go, so `wait` (340 rows), `rework` (101), `grant` (67), `sizing` (35) and
`classification` (17) are first-class kinds. `wait` and `rework` each carry more rows than
`field` at 25 and `gate` at 8, which the set already had, so neither is an edge case.
Architecture §32.3.1 holds the routing table, the counts and the
runnable partition script; the residue is five hand-written prose lines that resolve to `note`,
so the partition is total.

**The migration constraint is the whole risk.** An append-only log is never rewritten, so
every existing `comment` event stays. The reader needs an **alias**, not the
unknown-kind skip path: a `comment` resolves to the kind its body announces, and a
`comment` with no marker resolves to `note`. A skipped `comment` would silently drop
checkpoint and gate state for every work item older than the change, and the phase
derivation would read those items as never classified, never approved and never landed.

**Install the alias before switching the writer**, never together. A writer that switches
first emits typed events an unaliased reader drops. Architecture §32.8 carries the ordering,
why `snapshot.rotate()` must not become the migration boundary, and why the `LOG_GLOB`
contract does not change.

| Item | Value |
| --- | --- |
| Scope | `.basicly/core/kit/tracker/events.py`, `.basicly/core/kit/tracker/snapshot.py`, `src/basicly/owned_store.py`, `src/basicly/mirror.py`, `src/basicly/loop_state.py`, `tests/test_owned_store.py`, `tests/test_loop_state.py`, `tests/test_kit_tracker_snapshot.py` |
| Integrity | `consumer-surface`. The owned ledger format is a frozen surface |
| Depends on | B8.1, B8.2, B8.3 |
| Acceptance | WHEN a new prose event is written, THE WRITER SHALL use the `note` kind. WHEN a machine marker is written, THE WRITER SHALL use the typed kind for it. WHEN the fold reads a pre-existing `comment` event carrying a marker, IT SHALL resolve it to that marker's typed kind. WHEN the fold reads a `comment` event carrying no marker, IT SHALL resolve it to `note`. WHERE a `comment` event exists, THE FOLD SHALL NOT take the unknown-kind skip path. WHEN the alias resolves a marker family whose producer no longer exists, IT SHALL still resolve it |
| Demonstrated by | `uv run pytest tests/test_owned_store.py -k alias -q`, plus a fold over the committed ledger reporting the same derived phase for every issue before and after the change, compared against a snapshot taken before the change rather than against `basicly tracker shadow`'s `clean` line |
| Cost | a frozen surface changes. The alias is permanent, not a migration window |
| Buys | a reader selects machine state by kind instead of grepping prose, and the fold can refuse a malformed marker |

---

## B8.4 — Deprecate the folded record's `comments` key

**Why.** The folded record is emitted by `snapshot.record_to_dict` with a `"comments"` key,
validated on read back by `record_from_dict`, persisted into the derived `snapshot.jsonl`, and
surfaced by the kit tracker CLI's `show` and `list`. After B8 the key names a kind that no
longer exists.

**The key is not on `loop session --json`.** That surface carries no comment-shaped key at all
[measured 2026-08-17, `uv run basicly loop session <root> --json | jq -r 'paths(scalars)'`
filtered for comment, note and log returns nothing, against a positive control of its 21
top-level keys]. So the deprecation window is narrow and local to the kit CLI, and this item
is separable from B8 rather than part of it.

| Item | Value |
| --- | --- |
| Scope | `.basicly/core/kit/tracker/snapshot.py`, `.basicly/core/kit/tracker/cli.py`, `tests/test_kit_tracker_snapshot.py` |
| Integrity | `consumer-surface`. The kit CLI's JSON is a consumer surface |
| Depends on | B8 |
| Acceptance | WHEN a folded record is emitted, IT SHALL carry a `notes` key. WHEN a folded record is emitted during the deprecation window, IT SHALL also carry `comments` with the same value. WHEN a snapshot carrying only `comments` is read back, `record_from_dict` SHALL accept it. WHEN the window closes, THE EMITTER SHALL NOT carry `comments` |
| Demonstrated by | `uv run python .basicly/core/kit/tracker/cli.py show .basicly/ledger <record-id>` printing both keys during the window, plus `uv run pytest tests/test_kit_tracker_snapshot.py -q` |
| Cost | two keys for one value for one release |
| Buys | the last place `comment` appears as a definition on a consumer surface goes away |

---

## B9 — Remove the external tracker binary

**Why.** Architecture §37 is the whole account. Two defects found on 2026-08-16 moved this
from a plan to a priority, and the second is reproducible on this checkout: the vendor's
documented repair path *"rebuilds DB from JSONL"*, and the JSONL export carries **0** gate
results against hundreds in the database [measured 2026-08-16, the probe in architecture
§37.4: 389 in the database, 0 in the export, against a positive control of 24 distinct
export keys]. Running the documented recovery for a corrupted store therefore erases the
gate ledger the phase derivation reads the word "landed" from.

This is a parent, not a leaf. Its children are the five unported operations in §37.2 and
the remaining bypass routes.

| Item | Value |
| --- | --- |
| Scope | `src/basicly/br.py`, `src/basicly/mirror.py`, `src/basicly/owned_store.py`, `.basicly/core/kit/tracker/**`, `src/basicly/loop*.py`, `src/basicly/policy.py`, `src/basicly/decompose.py`, `src/basicly/supervise.py`, `src/basicly/merge.py` |
| Integrity | `consumer-surface` |
| Depends on | B8 |
| Acceptance | WHEN the engine reads a work item, IT SHALL read the owned fold. WHEN the engine writes a work item, IT SHALL append to the owned log and SHALL NOT spawn an external binary. WHEN the shadow differential runs before the flip, IT SHALL report clean and conclusive. WHERE an operation has no owned equivalent, THE FLIP SHALL NOT proceed until it does |
| Demonstrated by | `uv run basicly tracker shadow` reporting clean and conclusive, then `rg -c 'run_br\|try_run_br' src/basicly -g '!br.py'` returning 0, plus `uv run pytest -q` |
| Cost | the largest remaining item in the tree. Five unported operations, each a design question |
| Buys | the loop's state stops depending on unowned code whose documented repair path destroys the gate ledger |

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

## B2 and B3 — landed

Deleted rather than marked, on this file's own rule. `.scripts/check_code_citations.py`
gates the code-to-document direction as the `code-citations` verify check (the remaining
unresolved citations are its frozen, shrink-only debt), and `.scripts/check_mermaid.py`
renders every committed block through a pinned mermaid as the `mermaid` verify check.

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

## B6 — landed with B9

Deleted rather than marked. The external tracker binary is gone, so `CONTRIBUTING.md`
carries no version-pin sentence to correct: it describes the owned ledger.

---

## B7 — Move the CLI reference out of the architecture document

**Why.** Architecture §22 is marked as a target and says so in its first line. It is a
per-command behaviour table in a specification, it is the largest single section, and it
goes stale on every landing. It stays only because four gates bind on it.

| Item | Value |
| --- | --- |
| Scope | `docs/architecture/architecture.md`, `docs/reference/cli.md`, `.scripts/docs_claims.py`, `tests/test_docs_drift.py`, `basicly.toml` |
| Integrity | `engine`. The assertion targets move |
| Depends on | nothing — B2's citation gate shipped, so the assertion targets can move |
| Acceptance | WHEN the CLI ships a subcommand, THE `cli-commands` AND `cli-subcommands` ASSERTIONS SHALL check the CLI reference and not the architecture document. WHEN a subcommand is removed, THE REVERSE TRIPWIRE SHALL check the CLI reference. WHEN the architecture document is read, IT SHALL NOT contain a per-command behaviour table |
| Demonstrated by | `uv run python .scripts/docs_claims.py --check` green after the target move, plus `uv run pytest tests/test_docs_drift.py -q` |
| Cost | a new document, four gate retargets, and one more file in the documentation set |
| Buys | the architecture document stops carrying a class of fact that goes stale on every landing |

---

## B8.1, B8.2 and B8.3 — landed 2026-08-17

The three preconditions of the split are done and their entries are deleted rather than
marked, on this file's own rule that a backlog entry describing shipped work is noise.
One definition of the closed kind set (`basicly-vkh0.36`, `basicly-vkh0.43`), the fold's
three-way distinction between applied, delegated and unknown (`basicly-vkh0.38`), and the
marker-family list bound to a gate (`basicly-vkh0.37`). Architecture §32.8 describes what
each one left behind. **B8 itself is now unblocked.**

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

## B9 — landed

Deleted rather than marked. `src/basicly/br.py` no longer exists, every engine read is the
owned fold, every write appends to `.basicly/ledger/` through the engine seam, and
`ledger-fsck` gates the log on every commit.

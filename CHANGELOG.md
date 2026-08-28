# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

## v0.10.0 - 2026-08-28

Delta: v0.9.0..v0.10.0

### Added

- **The board snapshot can now carry the `lanes` section, and only when a caller supplies
  the lane facts.** `lanes[].phase` is required by the contract and its authority is
  `loop_state.read_node_state`, which reads the policy config's required-gate set - a
  source the file-only producer does not open. So the facts arrive as
  `board_fields.LaneFacts` from a caller that drives the loop, exactly as the supervisor
  lock facts already do, and with none supplied the section is omitted rather than filled
  with a derived phase that would diverge from the engine's for any unit owing validation.
  An empty sequence still emits `[]`, which is the different claim that the caller can see
  lanes and there are none (`basicly-06pvsc`).

- **`basicly status` names the agent-hook tier this machine actually delivers, instead of letting a
  projected file imply it.** The `Hooks` table's `activation` column was `-` for the `claude` and
  `copilot` managers: git activation was reported, and the two agent surfaces were reported as
  projected and nothing more. A projected hook only fires where its host runs, so on a machine
  without that host the file is present and enforces nothing — the built-and-never-connected shape
  this repo keeps rediscovering. Each agent manager now reads `active` or
  `unavailable (<host> absent)`, with a line under the table naming which surfaces are active, which
  are not, and that the git hooks remain the commit-time floor either way. `basicly status --json`
  carries the same two facts per agent manager as `host` and `surface_present`; the payload is
  additive, so `schema_version` is unchanged.

  The probe behind it is `hooks.agent_hook_surface_present`, which resolves the host binary through
  an injected `which` like `runner.is_available` does — the suite hides the ambient agent CLIs on
  purpose, so an injected resolver is the only way a test can assert either answer.

  **The enforcement itself is now tested by running it, not by reading it.**
  `test_projected_agent_hook_fires_and_its_refusal_reaches_the_agent` plays the host against the
  projected `.claude/settings.json`: it selects a group by its `matcher`, substitutes
  `${CLAUDE_PROJECT_DIR}`, runs the command verbatim on an `Edit` payload, and asserts the block
  code *and* the refusal text — the exit code alone does not discriminate, because
  `python <missing>.py` also exits 2 (`basicly-0p8n`).

  **Both hosts have a hook surface, re-probed 2026-08-15 against the installed binaries**: claude
  2.1.233 and Copilot CLI 1.0.79, whose `copilot help config` documents a `hooks` key and
  `disableAllHooks`. The 2026-08-08 "copilot has no hook surface at all" finding was an artifact of
  its probe and is already retracted in `.basicly/core/kit/tier/README.md`. What copilot still does
  not receive is the `protect-generated` guard — it gets only the telemetry hook — and that gap is
  `basicly-66ix`, not this change.

- **`tree-growth` is a `[[verify.checks]]` entry: the whole tree's growth is now a number, because
  every other structural gate is blind to it.** `module-size`, `comment-density`, `noqa-debt`,
  `vulture`, `wired-or-deleted`, `lint-imports` and `pyright` are each a per-file or per-symbol
  predicate, so a tree can add fifty individually compliant modules and every one of them stays
  green. That is what happened: `src/basicly/` went from 50 modules holding 408,954 tokens on
  2026-08-07 to 91 holding 476,002 on 2026-08-14, with all seven passing throughout.

  `.scripts/check_tree_growth.py` reports **net tokens over a seven-day window**, in the same unit
  `module-size` counts in, decomposed into what sits in modules that did not exist when the window
  opened, what modules present at both ends did, and what deletion removed. Net tokens rather than
  module count, and the decomposition rather than a mean, because those are the only readings that
  separate growth from redistribution — a module extracted out of another takes from one term what
  it adds to the other, leaving the net flat, while a compliant *addition* moves it by its whole
  size. Chosen against this repository's own history, and the two commits that fixed the choice are
  asserted in `tests/test_check_tree_growth.py`.

  **It reports and never blocks, including when it cannot reach a number.** D23
  (`docs/requirements/factory-loop.md` §15.7) makes a sizing control with no recorded correct firing
  observability; this one has no firing history at all. Its window is anchored on HEAD's own
  committer date rather than the wall clock, so one checkout always answers the same thing, and a
  checkout that does not reach back a week — CI clones the quality-gates matrix at depth 1 — says
  the window is uncovered instead of inventing a baseline.

  Like `module-size` and `noqa-debt`, this is basicly's own gate rather than something `basicly
  install` projects: a consumer's tree is its own decision, and the growth of this one is what the
  number is about (`basicly-5p49`).

- **The wall ranks by urgency instead of by uniform weight.** A green state costs one token and
  an exception expands: 36 named gate checks collapse to `GATES ● GREEN`, or to the failing
  names and only those. The ask band leads with `6 DAYS` rather than `149h 37m`. `RUNNING NOW`
  collapses to one line when nothing is dispatched, and the ready list takes the reclaimed
  width with fifteen untruncated titles where eight were truncated. Each loop phase carries a
  bar proportional to its share, and the status bar carries units closed today or says it
  cannot measure them (`basicly-7ogfbq`).

- **A closed record that produced no release note now refuses the cut.** `changelog.d`
  could only ever check a fragment that *exists*, so nothing bound on a record that
  produced none — and 19 of the 54 records closed for v0.9.0 shipped with no note,
  including the seven specialist agents and five loop skills `basicly install` vendors to
  every consumer. The release workflow reads `CHANGELOG.md` from the **tagged** commit, so
  a note written afterwards can never reach the published release. The new `release-notes`
  check ratchets it: a closed record whose declared `## Scope` reaches a shipped surface
  (`src/basicly/`, `.basicly/core/`, `README.md`, `site/`) with no fragment named for it
  and no parenthetical citation in a fragment body or in `CHANGELOG.md` is named and
  refused, at the commit that closes the record and again in `basicly release`'s own
  refusals. It judges only a record that declares a machine-readable scope, so a record
  that closed before that convention is not reported; the 145 already unaccounted for are
  frozen in `[tool.release_notes.frozen]` so the backlog does not block a cut while a new
  omission does; and a change genuinely invisible to a consumer is declared in
  `[tool.release_notes.invisible]` with its reason, validated against the population it
  exempts from (basicly-7phc).

- **`basicly catalog dump` prints the composed fragment selection with each item's origin.**
  Every planned output lists the axes it declares and every item it selected, each with the
  core or `.basicly-local` file it was read from, and an overlay override is named beside the
  core source it shadows - so debugging a projection no longer means reading the sources by
  hand. (basicly-8kqkxy)

- **A gate reconciles a record's declared `depends on` against the `blocks` edges it has.**
  Two sources held one fact and nothing compared them, so an inverted edge read as correct
  from either side and held a ready lane unreachable. The check names the record, the
  declared id and the edges it does have, and refuses an empty population rather than
  reporting a vacuous pass (`basicly-9yyj6i`).

- `basicly board serve --bind <ipv4>` binds a chosen interface address for a touch wall
  or a team display; the default stays the loopback, a wildcard or a hostname is refused
  before binding, and actions stay gated by typed confirm codes on any address. (basicly-bxk5g8)

- **`.scripts/headroom.py` reports both size ratchets in one command.** Measuring headroom
  took two commands, so an agent measured one and paid for the other — three times in one
  session. The report names every module's token headroom and its prose-share headroom side
  by side and ends with how many of the tree's modules sit close to a bound. Measured on this
  tree at the time it landed: 163 of 433 modules within 615 tokens or 1.0 point of a bound,
  20 at zero token headroom and 46 at zero prose headroom (`basicly-co64`).

- **The loop dispatches the `curator` at ship, so a shipped unit's release claims arrive bound
  to their evidence.** `curator` was the last loop role no code path could reach: the
  phase-to-role table named `ship` while `loop._on_ship` never called an agent, so the persona
  was authored, projected, vendored and inert. It is dispatched now, priced and bounded exactly
  as the validator's judges are — outside the write phases, past the grant halt, and skipped
  under the supervisor's landing pass, which has no watchdog or stream meter of its own.

  The reply is read into a `release-record` artifact, and the schema **refuses** it unless every
  claim carries a test, a command or a gate a second reader can re-run, and unless the claims it
  could not evidence are named rather than dropped. Three outcomes stay distinguishable on the
  ship's detail line — bounded, refused, and not attempted — because silence reads as the first.
  **It never fails a ship**: the package has already merged by the time it runs, so a refused or
  bounded curation costs the landing nothing (basicly-e2mz.23).

- **`interface-facts` now names the machine-readable documentation route for every dependency
  this repo declares, so rung 3 stops costing an open-ended search.** Twelve rows, each fetched
  rather than recalled on 2026-08-19: `llms.txt` plus per-page markdown for uv, ruff, Claude
  Code, the Anthropic API, Codex and GitHub Docs; Sphinx `_sources/*.rst.txt` and `objects.inv`
  for Python, pytest and the library dependencies; and git and pre-commit stated as rung 2,
  answered by the installed binary. Absence is controlled for — every host that 404'd on
  `llms.txt` was re-probed on a page it must serve.

  The table sits **below** the binary-first rule, with the incident that forces the ordering:
  a `uv` installed at 0.11.28 against 0.12.5 released, where the current documentation described
  a `uv init` default the installed binary did not have and `--help` could not reveal. It also
  states what to refuse — never cache or commit fetched documentation, never cite an aggregator,
  never record a fact without its version and date — and tells the reader to probe a row rather
  than trust it, because three of the twelve routes redirected on the day they were written.
  GitHub Docs' Search API is documented with the **undocumented `client_name` parameter** it
  requires: the example GitHub prints in its own `llms.txt` answers 400 without it. The probe
  behind the table, including the two claims it refuted, is
  `docs/research/2026-08-19-documentation-routes.md` (`basicly-e2mz.48.1`).

- Catalog sources may declare `token_cost:`, the always-on tokens they add per surface (a target name for a fragment, `listing` for a skill). `basicly catalog lint` measures the real cost and fails a declaration that has rotted past its tolerance; an absent declaration is reported without failing until 0.11.0, since the schema is a contract other repos author against. (basicly-e2mz.48.3)

- **`code-citations` is a `[[verify.checks]]` entry: a section mark written in code, pointing at a
  document, is now checked against the headings that document defines.** `docs-citations` only ever
  ran the other direction — a `file.py:line` written in a document — so a `§N` in a comment or a
  docstring was checked by nothing at all, and a mark that resolves to the *wrong* section is worse
  than none because it reads as correct (`basicly-e2mz.49`).

  Measured over tracked Python in `src/`, `tests/`, `.scripts/` and `.basicly/core/`: **370 marks in
  94 modules**, of which **220 reached no heading**. Two of them cite
  `gates-and-rework-design.md`, absorbed and deleted 2026-08-08; four in the shipped tracker kit read
  as the kit's own source document while meaning the architecture. Those are the citations the two
  document absorptions blocked on this check would otherwise have orphaned with every gate green.

  **A citable target is a document and a number, both nameable.** The document is a `.md` path on the
  citing line, or a path-prefix binding in `[tool.code_citations.bindings]`; the number must match a
  numbered heading — `## 4. Title`, `### 4.6 Title` — the document defines today, which is the surface
  the architecture's section 3 promises a citation may rely on. A mark missing either half is
  **unresolved** and is a finding, not a silent pass: `docs-citations` counts 32 citations it cannot
  verify and exits zero, and that is exactly the shape this gate refuses.

  A **binding** is one reviewable line that made the kit's 113 bare marks checkable, and it is
  ratcheted against `binding_count` in both directions — added quietly, one binding could make a whole
  directory's marks resolve against a document nobody chose. A binding whose prefix stopped matching
  anything is reported rather than silently satisfied.

  **A ratchet, not a ban.** The 220 already-unresolved marks are recorded per module in
  `[tool.code_citations.frozen]` and may only fall; a module absent from that closed list may carry
  none. **No `fix_command`, and the omission is the point**: a mark whose section was absorbed into
  another document has no derivable target, and repointing a number whose sentence also went stale
  repairs the pointer and leaves the false claim.

  Like `docs-citations` and `module-size`, this is basicly's own gate rather than something `basicly
  install` projects: a consumer's document set is its own decision with its own frozen list and its
  own bindings.

- A `retired-vocabulary` verify gate now refuses prose growth of the removed tracker's name in comments and docstrings, with git HEAD as the per-module baseline (`basicly-e90rue`).

- **VALIDATE now dispatches the `reviewer` agent, once per lens, beside the validator.**
  The agent was authored, projected to both agent roots and vendored to consumers, but
  `roles.ROLE_BY_PHASE` mapped a phase to exactly one role, so nothing could reach it.
  A phase now resolves through two tables: `ROLE_BY_PHASE` for the role that drives it,
  and `LENS_ROLE_BY_PHASE` for the role it fans out over `roles.REVIEW_LENSES`. Each
  review is dispatched with its own lens in the brief and records its findings under its
  own `[harness-review] lens=<lens>` marker on the unit; nothing merges two lenses into
  one ranking. The vocabulary ships as two axes — `correctness` and `security` — so an
  L3 unit pays two extra read-priced dispatches per VALIDATE advance, and L1 and L2 units
  pay nothing because they never derive the phase (`basicly-feje`).

- **A dependency edge can be retracted, so a decision that inverts one can be enacted.** The
  work tracker could add an edge and never remove one, which left an owner decision that
  *reverses* which of two records goes first with no safe route: adding the reverse edge without
  withdrawing the original closes a two-record cycle, and the cycle report cannot be relied on to
  refuse it. `basicly tracker write -- dep remove <record> <target> -t <type>` now records a
  retraction. It is a retraction and not a deletion — the ledger stays append-only, the fold
  answers with the edge gone, and the history still reads as asserted then withdrawn, which is
  the shape a tombstoned record already had. Two decisions are made explicitly: retracting an
  edge the ledger does not hold is **refused**, naming both records, because a typo in a record
  id would otherwise record a withdrawal of nothing while reading as success; and a
  `parent-child` edge is **not retractable**, because removing one re-parents a record and
  `basicly loop supervise` fans out over `parent-child` dependents, so it would silently change
  which records a supervised run touches (basicly-he6200).

- **A `[[verify.checks]]` check can declare `inputs`, and the pre-commit hook skips one no staged file matches.** A lane's commit runs the gates whose declared inputs intersect its diff plus every gate that declares none, and names each skip. `--mode full` (the landing, the push) ignores `inputs` and runs all of them, so nothing green rests on a skip (`basicly-j7spdb`).

- **`docs-citations` is a `[[verify.checks]]` entry: a `file.py:line` written in a document is now
  checked against the code it points at.** Nothing read one before. `docs-claims` gates generated
  blocks and `corpus-drift` gates an epic's problem statement, so a claim recorded in a requirements
  document on one day and refuted by the next day's commit kept asserting itself — measured on this
  repo's own plan, where four such claims sent a session at a P0 against a remedy the tree had
  already replaced (`basicly-miqr`).

  `.scripts/check_docs_citations.py` applies two exact rules and refuses to guess past them. A cited
  line must be **live code** — past end-of-file or blank is a citation that has certainly drifted. And
  when the citing sentence also names a **module-level** `def`, `class` or assignment of the cited
  file, in backticks or bare inside a fenced block, the cited line must fall inside that symbol; the
  failure prints the line the symbol moved to. A citation whose sentence names no symbol of the cited
  file is reported as *uncheckable* rather than as a pass, so the summary's coverage share can never
  be mistaken for the population. Module level only, and the module's own stem excluded, because a
  local named `total` or a word matching the filename matches half this repo's prose and would turn
  an exact rule into a coin toss.

  **A ratchet, not a hard gate.** Four citations were already stale in documents no single lane
  should rewrite, so the go-live debt is recorded per document in `[tool.docs_citations.frozen]` and
  may only fall — a document absent from that list may not carry one stale citation. **No
  `fix_command`, and the omission is the point**: renumbering a pointer whose surrounding sentence
  has also gone stale repairs the citation and leaves the false claim.

  Like `module-size` and `tree-growth`, this is basicly's own gate rather than something `basicly
  install` projects: a consumer's document set is its own decision with its own frozen list.

- **A supervisor can be stopped without killing the lanes it has in flight.**
  `basicly loop supervise` ran `while True` and returned only when every child of the
  root had closed, so the only lever short of the session finishing was a signal — and
  the lanes are `claude -p` subprocesses of that process, which a signal leaves killed
  mid-write or orphaned against a grant nothing is metering. Lock takeover was not the
  control it looked like either: a lock is stolen only from a holder whose heartbeat has
  gone stale, so a *working* supervisor could not be asked to finish. Two bounds now end
  a session between rounds, where nothing it started is still running.
  `basicly loop stop <root> --reason "<why>" [--by NAME]` writes a marker naming the
  requester and the reason, prints the lanes it is waiting to land, and returns once the
  session does: the round in flight completes, every dispatched lane lands, and no
  further lane is seeded. It refuses when nothing is supervising that root, because an
  unread marker would stop the next session started there before it ran a round.
  `basicly loop supervise --max-passes N` is the cheaper half — it returns after N
  rounds even with open children left, so a launch can commit to a bounded spend up
  front. Both exits are non-zero and name themselves on the pass narrative
  (`stopped:  …`), which is where the requester and reason are recoverable afterwards
  (`basicly-o40x`).

- **A run can now choose the model tier it dispatches at, without editing committed
  config.** `basicly loop supervise` took `--runner` and `--autonomy` and no way to name a
  capability tier, so dispatching at `maximum` — `claude-fable-5` on Anthropic — meant
  editing `[runner] default_tier`. The session-override module's own reasoning rules that
  out: editing the committed file changes behaviour for every consumer, while the whole
  point of the registry is that configuring one run should be one command rather than a
  config edit plus a revert the operator has to remember.

  `--tier {low,medium,high,maximum}` joins the other two on the same mechanism and in the
  same shared helper, so it reaches every subcommand that can dispatch an agent. It is
  validated against the known tiers **before** anything is applied, on the all-or-nothing
  rule the existing pair already follows, and it lands in every run record for free
  because the record builder stamps the active overrides centrally — an unrecorded
  override would leave two genuinely different dispatches behind indistinguishable
  records.

  It selects the tier for the **whole pass**, not per lane: runner selection resolves one
  spec per round. Two models can still appear on one board at the same time, because a
  lane card reads its model from that lane's own last run record rather than from the
  current pass — so a lane whose previous dispatch ran on one model renders it beside a
  lane dispatched now on another. Per-lane selection is a separate, unbuilt piece of work. (basicly-pmhmsp)

- **The harness board is a wall layout that answers four questions, not a dump of the
  schema.** The previous render gave every schema key a fixed-height box its content
  overflowed and repeated the same freshness sentence on all ten, so the page did not
  say what is being built, where the loop is, what is waiting, or what is in the
  backlog. It now draws eight fixed rows at 1920x1080 with no scrollbar: a watch band
  in the page's only alarm colour, a loop row counting each of the seven phases and
  marking where the lanes are, fixed-size in-flight cards beside the ranked ready set,
  a footer carrying the backlog with a closed bar and a per-priority histogram plus
  gates, spend and health, an event ticker, and the verdict's whole section roster. A
  region that cannot draw everything says `+N more` naming what it dropped, the
  freshness reading is taken once for the page rather than once per panel, an absent
  section still reads `not emitted by this producer`, a bar is still refused unless
  both of its terms were measured, and the layout reflows to one column below 1280px
  (`basicly-rbnz49`).

- **A published snapshot contract, `harness-board/v1`, and `basicly board validate` to check
  a snapshot against it.** The schema ships as a catalog source at
  `.basicly/core/schemas/board-snapshot.schema.json`, and a `board-schema` entry in the
  verify pipeline checks it on every run. A snapshot carries only `meta` as required; every
  other section is optional, so a producer declares what it can supply rather than filling
  fields it cannot know. Closed value sets are deliberately few — `phase`, `status`, `type`
  and edge kind are open strings that name this project's values as examples rather than as
  the enum, so a producer with its own vocabulary is not refused. **What this means for a
  consumer:** the contract is the interface, so a repository can emit a conforming snapshot
  from whatever work tracker it already uses and have it checked, without adopting this
  project's store (basicly-rn0o.1).

- **The snapshot contract is checked against a producer that is not basicly.** A
  fixture emits a `harness-board/v1` document without importing the engine, and the
  validator admits it, so the published contract is exercised as a foreign consumer
  would exercise it rather than only against our own producer (`basicly-rn0o.13`).

- **A `harness-board/v1` snapshot producer that reads files and spawns nothing.**
  `board_snapshot.build_document(repo_root)` folds a conformant snapshot out of three sources —
  the owned event log, `.basicly/usage/run-records.json` and `.basicly/usage/verify-run.json` —
  in **zero subprocesses** and **one** fold of the log, measured by a spy in
  `tests/test_board_snapshot.py` rather than asserted. The producer exists because
  `supervise.observe()` folds the same log **93 times** to answer one question, at 6.1 s; a whole
  snapshot on this repository's committed corpus is **81 ms** (median of 7) against a 500 ms cap.
  Nothing consumes it yet — `basicly board --out` is a later unit — so this adds a library surface
  and no command (`basicly-rn0o.2`).

  **The live-lock facts are an argument, never a read.** Reading the supervisor lock here would
  mean calling `supervise.read_holder`, and the supervisor emits a snapshot itself, so the import
  would close the cycle `supervise → board_snapshot → supervise`. Callers pass a `SessionFacts`
  carrying `supervise.LockInfo`'s own field names, and with none supplied the `session` section is
  **omitted** rather than filled with nulls or a guessed root.

  **Omit, never estimate**, because the schema has no field marking a value as estimated. A
  transcript-estimated dispatch is left out of `spend`, and where every dispatch is an estimate the
  section is absent rather than indistinguishable from a billed one. In a lane worktree
  `.basicly/usage/` does not exist, so `spend`, `health` and `gates` are all absent and the tracker
  half of the board still draws. `lanes`, `units` and `graph` are not emitted by this producer:
  `lanes[].phase`'s authority is `loop_state.read_node_state`, which needs a source outside this
  producer's three files.

  **The marker roster is bound by a gate, not by a hand-kept list.** `board_fields.MARKER_FAMILIES`
  must equal `.scripts/check_marker_families.FROZEN` — 11 declared plus 1 retired — and
  `tests/test_board_fields.py` asserts that by loading the gate **by file path**, since `.scripts/`
  is not an importable package and its gates import into `basicly`. All 12 are parsed, the retired
  `[harness-overrun]` included, and a malformed marker is skipped rather than raised.

  **A pending ask is a pairing, not a tally.** Reading every `[harness-wait]` request as open
  reports **140** on this repository's log against **1** genuinely pending; the test pins both
  against a frozen corpus under `tests/fixtures/board/ledger/`, with the answered side at **203**
  distinct ids so a parser that silently matched nothing cannot pass. Every string in the document
  passes `redact.redact_committed`, so no absolute path and no username reaches a board.

- **The board snapshot schema's field-selection figures now name the store this repo has.** Two
  `description` strings quoted `3336549 B` against `33745 B` — `98.9×` — which are the deleted
  external tracker's bytes. Against the owned ledger it is **5890340 B** against **44454 B** for
  the 236 active records at six selected fields, **132.5×**. The rule is unchanged; only the
  measurement behind it was stale (`basicly-rn0o.2`).

- **`basicly board --out <path>` writes the board as one self-contained HTML file.**
  The producer that folded the snapshot had no caller outside tests, so no snapshot
  could be produced and nothing rendered. The command now emits the page and the
  `harness-board/v1` snapshot beside it, and prints a per-source inventory naming
  each source it read. Every panel renders its own `generated_at` and a computed
  age, and a section the producer did not emit renders as `not emitted by this
  producer` rather than as a zero (`basicly-rn0o.3`).

- **`basicly board serve` puts the board on a wall display, read only.** It binds
  `127.0.0.1` and nothing else, answers `GET /` with the page and
  `GET /snapshot.json` with the `harness-board/v1` contract, and returns 405 to any
  POST — the action surface is a separate unit and a screen anyone in the room can
  touch cannot kill a lane. While a supervisor lock is fresh it serves that
  producer's snapshot bytes and folds nothing; otherwise it folds for itself every
  `--refresh` seconds (default 15, the supervisor's own heartbeat) and keeps the
  result in memory. The process takes no lock and writes no file, so a board can
  never be the reason a gate or a landing failed, and Ctrl-C reports how many
  refreshes it managed (`basicly-rn0o.5`).

- **A human can now act on a lane from the board, and the board still holds no authority of its
  own.** Every action the page offers is run by spawning the `basicly` CLI, which writes what
  that command has always written; the board decides nothing and may be removed without
  changing what any write means. `basicly board serve --no-actions` removes the route entirely.

  The anti-autopilot boundary is kept rather than worked around. The board never reads
  `.basicly/usage/checkpoint-confirms.json`, because a page that read it and offered a
  one-click approve would be relaying the confirm code to itself. It presents an empty box a
  human fills - deliberately more friction than a button.

  Three mitigations on the one `subprocess.run` behind it, each asserted in
  `tests/test_board_actions.py` rather than left to trust: the executable is resolved with
  `shutil.which` and is never a string, every field is matched against an id pattern that
  admits no leading `-` so a POST cannot smuggle a flag past argparse, and `shell` is never
  set on any path. (basicly-rn0o.6)

- **The supervisor tick writes a board snapshot.** A supervised pass now publishes
  `.basicly/usage/board/snapshot.json` on its own tick, temp-then-rename, so a
  reader sees the previous document or the new one and never a partial. A failure
  logs one line and never fails the pass (`basicly-rn0o.7`).

- **A rendered surface is not exercised until its rendering has been looked at.** The new
  path-scoped `rendered-surfaces` rule says so on the board modules, the templates and the
  site, and `.scripts/check_render_overflow.py` measures it: every element whose scroll size
  exceeds its client size *and* whose box hides the difference. A declared ellipsis and a
  scrollable box are not clips, and the script measures the viewport asked for rather than a
  window of that size. It fails rather than skips with no browser, and is deliberately not a
  verify check because continuous integration has none (`basicly-skg052`).
- **`release-notes` names the fragment the base branch already holds.** A lane branched
  before a record closed on base was refused every commit over a note that existed one tree
  away, and answered by declaring the record invisible with a control that was true at its
  branch point and false on arrival. The refusal now says which file and says rebase
  (`basicly-skg052`).

- **Each agent role now declares the skills its purpose names.** Fourteen of the
  twenty model-invoked skills reached no role, and five roles declared none at all,
  so guidance the engine inlines into a dispatch prompt never arrived. `catalog
  lint` now reports any model-invoked skill no agent declares, against an exemption
  list that names the operator and environment skills a lane role must not carry
  and says why (`basicly-sromom`).

- **The waiver count is ratcheted across the gates that grant it.** Each ratchet counted its
  own waivers and nothing counted them together, so pressure moved to whichever gate was
  cheapest to waive and no total ever rose. A waiver is now counted once, across every gate,
  against a frozen total that may only fall (`basicly-twfj`).

- **`basicly loop improve` runs the second loop shape: a control loop over a property of the
  codebase, rather than over a requirement.** The delivery loop takes a requirement and ships a
  change; this one holds module size against the 4,000-token agent working-set cap and chips at the
  standing debt on a schedule. Set point, sensor and dampener already existed and are used rather
  than restated — `read_cost.SCOPE_FILE_READ_CAP`, `.scripts/check_module_size.py`, and the frozen
  ratchet in `[tool.module_size]` that stops the property getting worse meanwhile. What was missing
  was the controller and the actuator, and `.scripts/improvement_controller.py` is both.

  **The engine disposes.** Selection is arithmetic over the sensor's measurements: the unwaived
  module furthest above the cap, ties broken by path, so two runs over one tree pick the same
  target and no model chooses it. It reads the sensor's *measurements* and never its findings — a
  frozen module sitting at 60,089 tokens is exactly what the ratchet permits and exactly what this
  loop exists to reduce, so a loop driven by the gate's failures would have nothing to do on a
  green tree. A waived module is never a target: the waiver is a recorded decision, and
  re-targeting it would re-open it every run.

  **One unlanded lane at a time**, and the bound is basicly-u2hl.23's `wip.WipAdmission` rather
  than a second record beside it. Its occupancy set is deliberately wider than BUILD's
  `wip.DOWNSTREAM_PHASES`: a lane this loop filed still counts while it is being built, because a
  second target selected over the same tree is the duplicate work the bound exists to prevent. A
  run with a lane open files nothing and names what to land; a run with none files exactly one.

  **The drop is reported.** One run selects one of sixty-nine candidates and prints the count it
  did not select — a silent top-1 reads as "nothing else is over the cap" (`basicly-u2hl.27`).

- **A handoff artifact kind with no producer is now reported as unwired instead of counting as a
  live contract.** Eight kinds are named and seven have a schema, which read as seven contracts;
  three run. The four schemas nothing records — `classification`, `change-shape`,
  `verification-evidence` and `validation-transcript` — resolved through the same seam the wired
  three do, so `handoff.adopted` answered yes for a kind no state produces and no state reads, and
  `handoff.record` would refuse a payload for an artifact that never travels.

  `handoff.PRODUCERS` declares, per kind, the `module:function` that records it or `None`, and
  `handoff.wired` is the predicate `_validator` consults before it resolves a schema file. So an
  unwired kind is inert at both ends, the way an uninstalled schema already was.

  **Declared, not derived from absence.** Searching for a caller cannot tell "unwired" from
  "probed wrongly": a search for a kind's own name returns the English word, six files for
  `classification` and five of them prose. A missing declaration is not ambiguous. Two states and
  no third — *why* an unwired kind has no producer is a backlog fact, and a copy of it here would
  go stale the day a record lands.

  The declaration is kept honest by its own test: each declared producer is read out of the
  package's source and shown to define the named function, to name that kind's own constant, and to
  be called. A renamed or dead producer therefore fails as a defect rather than demoting its kind
  to unwired, which would hand the fail-open answer straight back to absence. (basicly-u2hl.59)

- **The board snapshot now carries the `units` and `graph` sections, so a board can draw
  the work rather than only count it.** `units` is one field-selected row per drawn record
  at five fields, `title` the only prose admitted and bounded so a description cannot
  arrive by being called one; `graph` is the dependency edges among those records as
  triples. Both are the active cut rather than everything the log holds, which is the
  population C6 priced the payload on. Neither costs a second read: they come out of the
  one fold and the one event list the producer already had, and the edge reader is bound by
  test to the kit's own reader so a retraction cannot drift between them. `units` carries
  no `ready` and no `phase`, and `backlog` still carries no `ready` or `blocked` - each is
  the tracker's own derivation, and a second spelling is how two derivations come to
  disagree (`basicly-vhixrn`).

- **`basicly tracker write -- <subcommand> ...` makes one hand-authored tracker write through
  the engine seam.** Editing the append-only event log by hand appends events nothing
  validated, to a store with no undo; spawning a tracker binary beside the engine has the same
  effect, and three records on this repository's own tracker arrived that way and were the whole
  of what its store comparison could not reconcile. The verb routes the argv down the path the
  engine's own writes take, so the read-only guard, the argv classification and the event
  translation all apply to a human's write, and the seam's refusals land **before** anything is
  recorded: an unresolvable tracker mode and an argv the translator cannot represent are both
  refused ahead of the write rather than after half of it. The `work-tracker` skill names the
  verb, which is what makes it reachable to a dispatched agent rather than merely present
  (basicly-vkh0.24).

- A `pipe-status-guard` PreToolUse hook refuses reading a pipeline's exit status when a
  pass-through filter ends it (`cmd | tail` reports tail's status over a failed gate);
  it fires only where the status is actually read and names the redirect-to-a-file repair.
- `falsify-first` gains the rule that a probe must exclude the file defining its own
  vocabulary: the instrument is not a member of its own population. (basicly-xkqxp9)

- **A `mermaid` verify check draws every committed diagram with the renderer the reader's
  browser runs.** The architecture document carries 16 mermaid blocks and the README one, and
  nothing looked at any of them: a block with an error renders as a red box on the hosting site
  while every gate here stayed green. One revision named a `sequenceDiagram` participant `Loop`,
  which collides with mermaid's `loop` keyword — a parser caught that one, review did not.

  **The criterion is renders, not parses, and that distinction is measured rather than assumed.**
  `mermaid.parse` stops at the grammar and never runs the diagram's own `draw`. Three blocks
  parse clean and refuse to render on this version: a subgraph whose id repeats a node id, a
  `gantt` task with an unparseable date, and a `stateDiagram-v2` note on a state that does not
  exist. A check written to `parse` would have passed all three, and the tests run those three
  through both instruments so the claim stays a measurement.

  **The renderer is pinned to what the hosting surface actually serves.** GitHub Pages publishes
  `site/`, which holds no markdown, so it renders none of these blocks; the surface a reader sees
  is github.com's own markdown view, which draws mermaid from
  `viewscreen.githubusercontent.com/markdown/mermaid`. That bundle runs mermaid 11.16.1, so
  `package.json` pins 11.16.1, the check prints both numbers on every run, and a drift between
  them fails rather than being logged — a check pinned to the wrong renderer is a gate that
  agrees with itself. Nothing skips: a missing node, a missing `npm install`, a renderer that
  writes no usable report and a tree holding zero blocks all exit non-zero, because a skip and a
  pass are the same line in a log and an empty population is the collector breaking.

  The cost is a dependency addition an owner approved: `mermaid` and `jsdom`, 147 packages, no
  browser download — `@mermaid-js/mermaid-cli` was rejected for its `puppeteer` peer dependency
  (basicly-yy82zy).

- **The supervisor pass line now states each runner's own health and drift.** `basicly health`
  scored every agent off the run-record log and nothing in the engine read it, so a runner whose
  failure rate had moved was visible only to whoever ran the command — never to whoever reads a
  pass, where the band and spend numbers already are. `supervise.health_coverage` adds two lines
  beside them before anything dispatches:

  ```text
  health:   claude 0.78 over 163 runs (fail 18%, rework 17% — 18 bead(s) re-dispatched); manual
            0.99 over 194 runs (fail 0%, rework 2% — 3 bead(s) re-dispatched)
  drift:    REGRESSED claude: fail 80% over the recent 5 vs 16% over 158 baseline runs (+0.64)
  ```

  That is this repository's own log on 2026-08-14, not an illustration.

  It is **observability, not a gate**: a pure in-process read over
  `.basicly/usage/run-records.json`, so it spawns nothing, meters nothing and refuses no lane.
  D23 (`docs/requirements/factory-loop.md` §15.7) makes a signal with no recorded correct firing
  reportable rather than blocking, and this one has never fired in anger. The drift half prints
  both window sizes, because the flag only means anything with enough runs on each side of it.

  A repo with no log says so — `no run-records yet` — rather than printing a zero (`basicly-zdtx`).

- **`basicly tracker show <id>` answers what a record blocks, what blocks it, and what its
  children are.** Neither the engine's command nor the kit's own `show` rendered a single edge:
  both returned the folded record's seven keys with no `dependencies` and no `dependents`, while
  the engine's internal reader answered both directions off the same edge events in the same
  store. So no command-line surface answered the dependency question one record at a time —
  which is the question an agent orienting through the CLI asks first, and the reason a snapshot
  producer built over this surface would have emitted a graph with no edges.

  Both surfaces now carry both keys. Each edge names its type and the other record's status; a
  dependent also carries its title, because a caller listing children has no second read to
  reach for. **Both keys are always present and empty when the record has no edges**, so absence
  is distinguishable from a surface that never rendered them — the failure that prevents is a
  reader taking a missing key for "no blockers". A test holds the two producers to one shape over
  three records including a dangling edge, because they are two producers and not one: the kit
  cannot import the engine, so no single implementation is available from that side. The
  `work-tracker` skill, which listed six keys of a folded record, names both (basicly-ztik9a).

### Changed

- **The reader that reports a truncated handoff artifact now lives with the recorded form,
  and `handoff.py` is back inside the module size cap with no frozen baseline.** The
  cut-violation lookup moved from `basicly.handoff._cut_violation` to
  `basicly.artifact_record.cut_violation`: reading the retired `[harness-artifact]` marker off
  a stored row is the recorded form's job, and the ruling only needs the reason it hands back.
  Behaviour is unchanged — a body the per-event text cap cut is still refused naming the
  truncation and both byte counts, rather than reported as a schema violation. `handoff.py`
  falls 4504 -> 3946 tokens under the 4000-token cap, so the baseline `basicly-u2hl.59` froze
  it at is deleted rather than left licensing regrowth, and its prose share is unchanged at
  65.4% because the extracted unit was within a tenth of a point of the module's own share
  (`basicly-09lc5o`).

- **A supervised implementer now forks the session its predecessor seeded** instead of
  re-reading the repo from zero. Implementer and repair inherit; reviewer, validator,
  decider, retrospector, curator and decomposer stay cold, because independence is what
  those roles are for. Claude only, and a pruned seed re-seeds rather than losing the round. (basicly-2kh170)

- **A generated path now declares its own rebuild command, and a partly generated file
  is safe to declare.** `[worktree] generated_paths` and `[worktree] regenerate_command`
  are replaced by one keyed table, `[worktree.regenerate_commands]`, mapping each
  generated path to the argv that rebuilds it. The old keys are refused by name, because
  one repo-wide command was silently a no-op for any artifact it could not write — this
  repo declared `basicly build` and the implementation plan's `docs-claims` block was
  rebuilt by nothing. A landing rebase now runs each conflicted path's own command, and
  bounces to the lane rather than staging a path whose rebuild left a conflict marker
  behind, so a file that is generated in one marked block and hand-authored around it can
  be declared without risking the hand-written half (`basicly-3w51`).

- The context-occupancy meter moved to its own module below `loop` and `supervise`, so
  the engine's declared `loop -> supervise` import cycle is gone and the layering
  contract no longer carries its exemption. (basicly-bom07a)

- The decompose-plan skill tells a plan to name a child's own fragment file instead of a directory glob, and the work-tracker skill records that a status a record once held cannot be rewritten (reactivate with in_progress). (basicly-d7rxd4)

- `basicly brief <id>` now names the ground the lane may not touch: the paths each still-open sibling of its root declares, the paths this landing admits from the lane itself, and `.basicly/usage/needs-input.json` with fact `scope` as the route when the work genuinely needs a sibling's path. A brief for a lane whose root has no open scoped sibling is unchanged. (basicly-dy4f94)

- **BREAKING: the `architect` agent can write, and the one file it may write is the
  architecture document.** `tools` is a vendored consumer surface — `basicly install` projects
  it into `.claude/agents/` and `.github/agents/` — so a consumer who upgrades gets an
  architect whose tool list is `[Read, Grep, Glob, Bash, Write, Edit]` where it was
  `[Read, Grep, Glob, Bash]`. The role named for architecture could previously only ever
  return a backlog about a document somebody else had to write.

  **The narrower constraint is now in the role's instructions rather than in its tool list**,
  and that is the part to read before upgrading: the agent is told it writes exactly one file,
  the architecture document, and is read-only everywhere else, but nothing in the projected
  `tools` allowlist enforces the *which file* half. If your repository depended on this being
  one of the agents that mechanically cannot edit, that is what changed. The trade-off is
  recorded rather than hidden: a document author differs from a tree surveyor in both tools and
  artifact, so it could have been an eighth role, and widening this one was the choice taken
  (basicly-e2mz.23).

- **The always-on instruction layer now says what counts as *authority* when checking whether
  a capability already exists.** Its reuse rule said *"grep for the helper, skill or gate
  before proposing one; absence needs a probe"* — and an agent followed it, grepped the
  config file, read `--help`, and concluded a feature was missing that had shipped long ago.
  The key it needed is read by the config loader and appears nowhere in the config file, so
  every honest reading of the documented surface said "absent".

  The rule now reads: **prove a capability absent before building it; the authority is the
  code that reads it, not the docs or `--help`.** A live key can be undocumented, and a
  missing flag is not a missing feature.

  It was added by **removing**, not appending — the layer had eight characters of headroom on
  its tightest surface, and the tightest surface binds for anything always-on. The retired
  fragment was a generic retrieval ladder: *"find files by name, localize with focused
  search, read only the ranges you need."* That is agent hygiene rather than repo knowledge,
  and reading the narrow range and stopping is precisely the habit that hid the key. Retired
  rather than deleted, so the reason stays on the record.

  Net effect on every projected surface is smaller, not larger: headroom rose from 107 to
  224, 209 to 326, and 8 to 125 characters. (basicly-grpzkw)

- **A `change-summary` artifact carries a changed-path count and digest instead of the
  path list.** The list was the only field that grew with the diff — 4096 of the largest
  recorded summary's 18555 bytes were sorted paths — and it is the only field a reader can
  recover from the `commit` the same payload carries: `git show --name-only <commit>` for a
  build that committed once, `git log --name-only <base>..<commit>` otherwise, checked
  against `changed_digest`. A 400-file landing now stores a body under 1 KB. Summaries
  recorded before this are still accepted, so nothing already handed on is refused
  (`basicly-gvlpxm`).

- **The event log's durability bound is measured rather than asserted, and the defect it was
  filed for does not exist.** `events.py` states the choice - no `fsync`, the push is the
  durability boundary - and nothing exercised it, so the bound behind that sentence was a
  claim. It is now five tests. What they establish:

  The append path asks the platform for no sync at all: a spy over a whole `append()` of five
  drafts counts zero `fsync` and zero `fdatasync`, against a control that counts one on a
  deliberate sync, and a tokenized scan excluding comments and strings finds no sync call on
  the write path - so the module docstring's own sentence cannot satisfy the probe. An
  interrupted batch is a **whole-line prefix**, never a hole and never a tear: a batch under
  one buffer chunk is all-or-nothing, and a larger one loses a suffix of complete lines that
  folds with no fork and no quarantine.

  **That refutes the record this was filed under.** `basicly-vkh0.30` holds sequences 1-8 and
  10-34, and the two survivors around the gap are **one file line and 525 microseconds apart**,
  while one append over that ledger measures 54-61 ms - so they were minted in one batch and
  written by one call. A partial batch truncates a suffix, so an interior line whose successor
  survives cannot be lost that way, and **an `fsync` would not have prevented it**. The loss
  happened after the bytes were in the file. The class moves from an unflushed write to a
  post-write mutation; the cause stays unidentified, as the record said.

  The useful half is that the next one is already detectable and is now pinned. The event above
  a hole carries totals a fold of the survivors cannot reach, and the next append restates from
  the fold, so exactly one event disagrees - measured live as one disagreement across 6,263
  events, one sequence hole, nothing quarantined. `BUFFER_CHUNK_BYTES` is asserted against
  `io.DEFAULT_BUFFER_SIZE`, having had exactly one occurrence in the tree before this: its own
  definition (basicly-mbkqxi).

- Worktree provisioning now copies `node_modules` from the base checkout or a sibling worktree whose `package-lock.json` is byte-identical, instead of running `npm install` in every lane; a differing lockfile still installs in full. Measured on this repo: a second worktree provisions in 1.4s against 6.2s. (basicly-oqspon)

- **Every agent source must declare a model tier, and `catalog lint` now refuses one that does not.**
  The tier vocabulary is `low`, `medium`, `high`, `maximum`. The rule it enforces is that a dispatch
  with no resolved tier is a defect rather than a default: an omitted tier inherits the spawning
  session's model, which is usually the most expensive one, so the routing rule defeats itself in
  silence.

  **A consumer inherits this.** An overlay agent under `.basicly-local/agents/` that declares no
  tier now fails `basicly catalog lint` with a message naming the file and the allowed values. The
  check walks every agent root, so core and overlay get the identical diagnostic — the asymmetry
  where a rule bound on core sources and not on overlay ones is the same one already closed for the
  tier vocabulary.

  Two shipped roles had no tier and now do. `code-reviewer` and `security-auditor` are both `high`,
  each argued against the tiers the other roles already declare rather than assigned: the
  hand-invoked review path must not be weaker than the engine path on the same diff, and a role with
  read-only tools has no external oracle to check its own inference, so its failure mode is a silent
  false negative.

  **What this does not do, stated because the gap is easy to misread.** The declaration is now
  mandatory and checkable. It is not yet effective: no spawn in this repository reads the tier, and
  `basicly-a3yi` is the open work that injects it into a projected surface. A declared tier reaches
  no model today (`basicly-plhx`).

- **The set of handoff artifact kinds is written down once.** `handoff.PRODUCERS` is now the
  only enumeration of it: the schema suite derives the kinds it exercises from that declaration
  instead of keeping a second hand-maintained tuple beside it, and the one declared kind with
  no schema authored for it is named against what the schema directory actually lacks. A ninth
  kind added to the declaration is exercised by construction, or named — it can no longer enter
  one list, miss the other, and read as a live contract because it appears in a list
  (`basicly-qnt8ng`).

- **The harness board's design now makes the `harness-board/v1` snapshot the only interface, and
  the contract readable without basicly.** `docs/requirements/harness-board.md` is revised so every
  consumer reads a snapshot document and nothing else, basicly's producer is one implementation
  rather than the definition, and a foreign producer gets a stated six-clause adapter contract plus
  a conformance kit that proves it. Two success claims were measured false and are fixed with a
  named remedy: `basicly board validate` answers `not-installed` and exits 1 in a directory with no
  catalog, so the check that was supposed to prove independence *was* the runtime, and the contract
  was distributed only to repositories that had already installed it. The remedy is a standalone
  single-file conformance script under `.basicly/core/kit/board/`, and the snapshot schema freezes
  under its own `harness-board/vN` version rather than folding into basicly's semver. Wall mode and
  the action surface are back in scope with the superseded four-unit decision recorded rather than
  deleted, the conformance kit moves from last to second in the build order, and the marker-family
  set and the wall's idle state are settled by measurement. **What this means for a consumer:** a
  repository that never runs `basicly install` can emit a conforming snapshot from whatever work
  tracker it already has, check it under a bare `python3`, and get a working board
  (`basicly-rn0o.10`).

- **A record may no longer be closed against a demonstration command that selects no test.**
  The plan gate checked the `demonstration` field's *form* — present, one line, something
  backticked — and never ran it, so a `uv run pytest <file> -k <expr>` whose expression matches
  zero tests passed. Measured over one session, five records were closed or worked naming
  exactly that, against positive controls collecting 210, 142, 87 and 23 tests in the very files
  they named: every real regression existed under another name and passed. So the field was
  refused for being absent and accepted for being wrong, and a third form rule could not tell a
  command that selects nothing from one that selects everything.

  `demonstration_proof` runs the criterion as `pytest --collect-only` with an allow-listed argv
  and refuses only on pytest's exit code 5, *no tests collected*; a missing instrument fails
  open rather than refusing an honest plan.

  **Where it refuses is the load-bearing part.** The first cut refused at plan time and was
  wrong: probed against three records filed that morning, two honest plans were refused, because
  at decomposition the test does not exist yet. Plan time now *reports*, in one line naming the
  children whose demonstration collects nothing and saying that this is fine for a test the plan
  will write and a typo otherwise. The **closing** advance refuses, ahead of every side effect,
  on the ground that a record claiming to be done was supposed to have written the test it
  names by now — and the refusal says so, with the two repairs. Measured over this repository's
  backlog when it landed: 17 records carried a demonstration, 1 collected nothing and was open,
  and 0 closed records would have been refused (basicly-u2hl.58).

- **The owned tracker's event vocabulary splits: `note` carries prose, `checkpoint` and `artifact`
  are typed machine state the fold reads by name.** One kind carried both before — 2,667 of this
  repository's 5,752 ledger events are `comment` [measured 2026-08-18], holding the prose a human
  wrote *and* every marker the loop derives state from — so a reader could not select machine state
  without grepping a free-text body, and the fold could not refuse a malformed marker.

  A folded record now answers two more questions directly: `checkpoints` maps an approved
  checkpoint to the approver the event named, and `artifacts` maps a handoff artifact kind to the
  last body recorded under it. Both are carried in the derived `snapshot.jsonl` and in a rotation
  checkpoint, because the resumed fold reads a checkpoint rather than the archive: one that dropped
  `checkpoints` would read an approved item as never approved. An artifact body sits outside the
  free-text cap, so it is stored whole rather than cut at 4,096 bytes.

  **`comment` is aliased, never retired, and no line on disk changes.** The log is append-only, so
  every `comment` event stays exactly as it is and folds to the same work log a `note` folds to —
  asserted as state equality between two ledgers written in the two spellings, because the
  unknown-kind skip path would have silently dropped the prose history and the checkpoint markers of
  every item older than this change. The kit's own writer records `note` from now on; the `comment`
  subcommand keeps its name, which is a consumer surface and moves under its own window.

  **What this does not do.** The `br` mirror seam still writes `comment`, deliberately: the reader
  must accept both spellings before any writer switches. The remaining kinds of the specified
  vocabulary — `decision`, `scope`, `wait`, `grant`, `rework`, `sizing`, `classification` — and the
  reader that resolves an existing marker body to its typed kind are not here; nothing yet reads a
  checkpoint or an artifact off its kind rather than out of prose (`basicly-vkh0.30`).

### Removed

- `docs/requirements/factory-loop.md` is deleted. Its five undocumented decisions live in
  the architecture as D-37 to D-41; D33's branch-home clause is superseded inside D-26.
  Every live code, gate and config citation now points at the architecture section that
  carries the rule; dated history keeps its original wording. (basicly-1hp91f)

- **BREAKING: the `code-reviewer` agent is removed; `reviewer` supersedes it.** `code-reviewer` was
  projected into `.claude/agents/` and `.github/agents/` and vendored to consumers by `basicly
  install`, so this deletes an agent you may be invoking by name today. `reviewer` does the same job
  with the stronger contract: it reviews **one named lens** and reports on that axis alone, with a
  severity on every finding and no ranking merged across lenses — a change can pass one axis and
  fail another, and reranking lets the strong axis mask the weak one (`basicly-e2mz.5`).

  **What to do.** Ask for `reviewer` instead, and name the lens you want: `correctness` or
  `security`. Those two are the whole vocabulary. If you name none, it takes one, says which, and
  answers for that axis alone rather than covering both in one reply. It fetches its own diff
  (`git diff HEAD`, or the range or component you name), so the ad-hoc path that `code-reviewer`
  served still works without a VALIDATE dispatch behind it.

  **The old name still resolves.** `roles.resolve_named_role` redirects `code-reviewer` to
  `reviewer` before it checks whether the file is there, so a caller holding the retired name gets
  the replacement rather than a silent fall back to an unspecialised runner. The supersession is
  also stated in `reviewer`'s projected `description`, which is the surface your host matches for
  delegation and lists to you.

  **One thing `basicly install` will not do for you.** Agent projection prunes a projected file only
  when a technology selection excludes its source, so an existing install keeps an orphaned
  `.claude/agents/code-reviewer.md` and `.github/agents/code-reviewer.agent.md` after the upgrade.
  Delete those two files by hand. A fresh install never writes them.

- **The external tracker binary and its store are gone from the runtime path entirely.**
  The cutover ladder collapsed to its last rung: `[tracker] mode` now accepts exactly one
  value, `owned`, and the `external` and `dual` modes are gone with the store they named.
  The engine reads and writes the owned append-only event ledger under `.basicly/ledger/`
  and nothing else. The `.beads/` directory, its ignore rules, the binary's installer, its
  tool skill and the five skills that named it are all removed, and the commit gate reads
  the owned ledger instead. **What this means for a consumer:** installing basicly no
  longer installs, pins or upgrades a third-party tracker binary, and no command shells out
  to one. The `mode` key itself is kept rather than deleted from the schema, so a repository
  that already committed `mode = "owned"` is not refused as declaring an unknown name
  (basicly-vkh0.42.7).

### Fixed

- **The Python guidance no longer tells an agent to write a waiver the gate refuses.**
  `python-guidelines` is path-scoped on `**/*.py`, so every agent touching Python loads it,
  and its waiver recipe was three weeks stale in three ways at once. It omitted the kind, so
  its literal example parsed as unclassified and `waivers.py` rejected it - *states no kind,
  so nothing says whether this is permanent or owed back*. It named `waiver_count` under
  `[tool.module_size]` in `pyproject.toml`, the shared anchor that bounced three of five
  lanes on one day and that a per-record `count_delta` in `basicly.d` replaced. And it never
  mentioned rebaselining, which is the ordinary permitted route - already used 50 times
  across 26 entries, requiring a reason and a base commit, counted and printed on the pass
  line rather than silent.

  The cost was measured, not supposed. Three of four agents in one parallel pass
  independently reported the two size ratchets as a systemic blocker and spent budget on
  them rather than on their task. One earlier unit was a total loss to it: it enumerated all
  8.4 million subsets of a file's 26 natural blocks against the gates' own measurement
  functions, found 225,756 that satisfy both ratchets and not one that is a nameable
  responsibility, and reverted - on a margin of 28 prose tokens.

  The guidance now gives the economics before the remedy. **Extracting is not free and two
  in three natural cuts make it worse**: removing a unit raises the parent's prose share
  whenever the unit is prose-lighter than the parent, so a cut that satisfies `module-size`
  breaks `comment-density`. Measured over 3,588 real top-level definitions in the 68 frozen
  oversized modules, only 34.4% are prose-heavier than their parent and so satisfy both at
  once. Then rebaselining, with its two required inputs. Then the waiver, in both accepted
  spellings - `cohesion:` for permanent and `cost(<record-id>):` for debt that expires when
  that record closes. And the trap that makes the obvious move wrong: **a waiver on a frozen
  module replaces its frozen entry outright**, so waiving a module far above the cap deletes
  its ceiling to buy a few hundred tokens.

  Checked by feeding every waiver example in every surface to the gate's own parser rather
  than by reading them - three examples, zero unclassified, on the catalog source and both
  projections. Sweeping the whole file instead of only the edited passage is what found a
  third stale kindless example that the original finding had not named. (basicly-03ykuf)

- **A supervised pass now dispatches every ready lane while the review queue has room.** The
  downstream-WIP bound charged a pass for its own admissions, so six ready lanes under a limit
  of 5 started 5 and refused one against a limit nothing stood at. `wip.admit` gates on work
  already downstream: below the limit all start, at it none does. (basicly-08rnmd)

- **A lane that is running no longer renders as one that has done nothing.** The board's
  in-flight card carried five of the sixteen properties the snapshot contract declares, so
  five of its six cells read `not measured` while the supervisor's own terminal printed
  those very figures for that very lane in the same second. The cause was the tier it read
  from: the card was built from the tracker binding alone, and that binding holds only the
  **last finished run** — which a lane on its first dispatch does not have.

  Three tiers now supply it, each asked for what it actually holds. The live event stream
  holds what a running lane has spent and the last thing it said; it is process-local to
  the supervisor, so it answers where the producer is the supervisor's own tick and is
  empty elsewhere rather than zero. The last run record holds a finished dispatch's cost,
  occupancy and duration exactly. The tracker binding holds the branch, the status and the
  agent.

  **A live lane does not inherit the previous dispatch's figures**, and that is the whole
  care in the change. Cost and occupancy are per-dispatch, so carrying them forward prints
  last run's spend under a heading that says the lane is running now. The agent and the
  model do carry, because a lane keeps its runner between dispatches.

  The activity line is the field with no substitute. Elapsed time and spend say a lane is
  alive and expensive without saying whether it is stuck.

  One rule was almost broken by its own implementation. Tokens has two sources and the
  live one wins while a lane runs, and preferring it on *truth* rather than on *presence*
  handed one window straight back to the previous dispatch: a lane's stream is published
  the instant the dispatch starts, so it reports a real `0` until the first turn is
  metered, and a falsy test resolved that to the last run's total — a card reading ten
  million tokens in a lane's first second. Tokens now obeys the same rule as cost and
  occupancy, and two tests pin it, one of them the window with a previous run to fall back
  to. The falsy form kills two of the three, so they discriminate on the rule and not on a
  value.

  Live elapsed time is deliberately still absent: no start time exists on the lane's
  stream, on its tracker view, or anywhere on disk, because the run record is written after
  the process ends. That is recorded as a follow-on rather than left as a silent gap. (basicly-0hxck3)

- The board action band no longer alarms on a checkpoint a live grant already delegates, or one whose record has closed; a queued decision still renders regardless of any grant. (basicly-0i86tl)

- **The wall board no longer cuts a region off the screen when a producer emits one more of
  anything.** The layout stated six pixel row heights, each measured against the tallest content
  that row could hold *on the day it was written*. The fixture behind it carried 12 gate checks;
  this repository's own snapshot carries 36, so the gate strip grew four rows, pushed `HEALTH`
  past the bottom of the footer, and cut the caption under the loop strip to a partial line.
  Measured against the pre-change template, the repository's own snapshot clipped **five** of the
  eight regions - `head`, `loop`, `foot`, `tick` and `inv` - and four of them were already one
  line short on the 12-check fixture the layout shipped green against.

  Every row is now the height of what it holds, and what it holds is bounded by a capacity the
  model states. `GATES` draws a reserved grid of `GATE_COLUMNS` x `GATE_ROWS` cells whether or not
  the checks fill it, so the strips below it cannot move when the count above them changes, and it
  says `+N more checks` for the rest. `HEALTH` and the priority histogram gained the same
  treatment - one is a line per agent and the other is keyed by the producer's own label
  vocabulary, which the schema deliberately does not close, so neither had a length the page could
  assume. The gate strip's mode and stamp moved from two cells to the strip's caption, because a
  two-line cell among one-line ones takes a row the grid had allotted to a check. A lane card now
  puts its id and its phase on one line and its six figures on three, because the in-flight row is
  the one region that absorbs the wall's slack.

  **A pixel tuned against today's count is the same defect one number along**, so the row test
  asserts that no wall row states a length at all, and a new `dense-v1` fixture puts every capped
  population over its cap at once - 40 gate checks against the tree's 36, six agents, ten priority
  labels, seven lanes, all seven phases counted - and asserts each one names what it dropped. Both
  assertions fail on the pre-change template and model. What a test cannot show is that the result
  fits 1080px: that was measured by rendering headless at 1920x1080 and 1200x900 and reporting
  every element whose scroll size exceeded its client size, across five fixtures and the
  repository's own snapshot. Zero, against five clipped regions before. (basicly-0jzq6g)

- **A running lane's card now shows the unit title, its phase and how long it has run, and its full activity note.** The card led with the tracker's own status and a bare id even while an agent worked, and a parked lane's stale tokens still drew the pulse of a live one; it now names the unit, states liveness plainly, and expands to the note in full. (basicly-0xtzf1)

- A landing now asks `release-notes` about the lane's own still-open record and refuses before the merge when it owes a note, instead of admitting the lane and failing the commit that closes it once the worktree is gone. (basicly-18iz59)

- `basicly loop advance` no longer blocks for input when a repair brief is stale against the branch head. The read that judges it stale has already consumed it, so there is no gate left to re-run: the advance now discards the brief, records why on the lane, and continues to the landing in the same invocation. (basicly-1djm17)

- **A repair that re-lands from validate records the `change-summary` for the landing it just
  performed.** The landing path has two merge call sites and only one recorded.
  `loop._verify_and_land` merges and calls `_record_change_summary`, which after
  `basicly-gvlpxm` correctly records the head the merge took; `loop._repair_from_validate`
  called `merge.merge_worktree` directly and recorded nothing. So after any repair re-land the
  artifact still described the first landing - `gvlpxm`'s own defect statement word for word,
  reached by the other route. `gvlpxm`'s own repair demonstrated it: its summary still names
  `d3422f81`, the pre-rebase head, while the fix reached main as `7381a145`.

  The changed paths are read **before** the merge, which is the ordering `_changed_paths`'
  docstring requires - afterwards the changed set is whatever else landed alongside - and a
  failed merge records nothing, leaving a true summary of the first landing rather than
  replacing it with a summary of nothing.

  **The record posed a design question and declined to answer it; its acceptance criteria
  answer it.** The two readings were that a re-land should re-record, or that the phase model
  is wrong to let a merge happen outside the state owning the artifact. The criteria choose the
  first, and it holds on its own terms: `handoff.record` is content addressed rather than write
  once, so the corrected payload is a second event and nothing is overwritten.

  **Nothing exercised this path at all.** A search of `tests/` for `_repair_from_validate`'s
  own block message returns nothing, against a positive control finding the string in
  `loop.py` - which is why the missing call went unnoticed through two records about the same
  artifact. The first test pair written for it did not discriminate either: reading the paths
  *after* the merge still passed, because the fixture pinned `branch_changed_paths` to a
  constant. The changed set now differs across the merge by injection, so *when* it is read is
  observable, and that mutation fails.

  `tests/test_handoff_states.py` crossed the 4,000-token cap under the new pair, carrying a
  third responsibility by then. The entry-refusal tests moved to `tests/test_handoff_entry.py`,
  their second move for the same five tests - which is `basicly-e2r08j`'s mechanism exactly:
  every split raises both halves' prose share, so the module that receives a section is the
  next one to overflow (basicly-3katht).

- A landing that fails its verify gate now briefs the repair with each failing check's own command and captured output instead of the whole-suite command and an empty string, and `verify-run.json` no longer records a blank `detail` for a failed check — it names the argv that reproduces it. (basicly-3oxf0d)

- **The two vocabularies sharing the provenance key are reconciled, so every folded edge is
  accounted for.** `migrate.PROVENANCE_KEY` and `provenance.KEY_LABEL` are literally the same
  string: the engine's write seam stamps *who wrote the event* into the field the fold reads as
  *how strong the evidence is*. Two axes, one name, and never reconciled. Measured on this
  repository's log: 142 edge events carried `engine` or `dual-write`, folding to 133 edges
  disposed `decide` for want of a vocabulary rather than for want of a fact, which is why
  `gating_edges` read 932 of 1065. It now reads **1065 of 1065**, and `unknown_labels` is
  empty.

  The two writer identities gate because of what they mean, not as a convenience: an event the
  engine's own seam appended is one a command asked for, which is the claim `EXTRACTED` makes.
  This widens the gating set by **two exact strings** and keeps the rule that only an exact
  known string gates - a near miss - `engine` with a trailing space, `dual-writer` - still routes a decision, and
  a test asserts that, because a prefix match here would be a fail-open on the one gate that
  decides whether an edge may hold up a landing. The blast radius was measured before the
  change: `gating_edges` has no production consumer, so resolving the 133 could not start
  gating anything today.

  They are counted in a new `EdgeFold.writer_labels` rather than folded into `unknown_labels`,
  because an edge that carried a writer identity never carried an evidence label and one count
  for both would say it did. The agreements the kit cannot enforce for itself - it may not
  import `basicly` - are pinned from the test side, which is the only place that can see both:
  `WRITER_LABELS == {owned_write.OWNED_PROVENANCE, mirror.MIRROR_PROVENANCE}`, and
  `KEY_LABEL == migrate.PROVENANCE_KEY`, so a later split of the key fails there first.

  This needed `provenance.py` split: it sat at 7,890 tokens, exactly its frozen baseline, so
  the vocabulary could not gain an entry. It is now 6,686 with the vocabulary and the payload
  key names in `labels.py` at 2,979. **A real gain beyond the size:** `provenance` and
  `differential` read one `DIALECT_KEYS` table instead of two copies, which is
  `basicly-oii83r`'s root cause removed rather than patched on both sides. Two standalone-kit
  fixtures enumerate the files a consumer copies, and both refused the new module until it was
  named - the control working, on the one constraint the kit cannot check from inside
  (basicly-493g5f).

- `--allow-retry` no longer degrades an L3 session to L2: the session-wide escalation
  scan now counts charged rework (attempts minus granted allowances), matching every
  other consumer of the cap. (basicly-54t8w5)

- **A repair brief the branch has moved past is refused as stale, and a repair that committed
  nothing is named rather than charged.** Observed on `basicly-gvlpxm`: its worktree still held
  a brief asking for the *post-regeneration branch head*, which had been fixed and landed hours
  earlier as `merge.MergeResult.landed_head`. Advancing would have dispatched a full metered
  repair for finished work. Nothing invalidated a brief when its defect closed by another
  route - not the landing that carried the fix, not the gate, and not the brief's own reader.

  **The wedge, which is why this was not merely wasteful.** A repair that finds nothing to do
  commits nothing, so the branch carries nothing its base does not hold, so the next advance
  takes the same branch and the same brief again. The brief is consumed on read, so the
  following advance falls through to `_rework` and charges the last slot for a round with
  nothing in it.

  The signal needed no clock. A brief now records the branch head it was written against, and
  a head that has moved means that work landed by some other route - the only fact that changes
  when work lands. Both halves fail **quiet** on anything short of proof: a brief written before
  the field existed carries no head, and a ref that will not resolve answers None, and neither
  is evidence of staleness. Refusing a repair on the reader's own uncertainty would strand work
  a red gate really does owe, which is the opposite failure and the more expensive one.

  **Where the code went was decided by a linter, and it was right.** `_repair_in_place` sits at
  exactly the six-return budget `ruff` PLR0911 allows - the same budget that forced
  `_repair_outcome` out of it under `basicly-dbbh` - so the staleness refusal widens the
  existing early-out rather than adding a branch, and the committed-nothing check went into
  `_repair_outcome`, whose stated job is what a finished repair leaves the loop blocked on. Both
  refusal messages live in `repair_brief.py` beside the predicate that raises them rather than
  at the call site, and a `landed=(branch, head)` tuple collapsed to a branch name once it was
  clear the brief already records the head to compare against (basicly-59fkfu).

- **A close carries its reason onto the record, and a create naming no title is refused.** Two
  defects on one surface, both found by using it: `basicly tracker write -- close <id> --reason
  "..."` printed `recorded:` and the reason went nowhere, and `basicly tracker write -- create
  --help` **minted a record** carrying nothing but its provenance.

  The reason was dropped by `mirror._close_drafts`, whose docstring justified not mirroring it
  *as a comment* - correct, since br records it as a field, and a comment row would be a
  difference the mirror invented rather than found - and was silent on the field. The kit
  already models it (`commands.CLOSE_REASON_FIELD`) and `commands.close` writes it, so the
  route existed and nothing used it. **Measured on this ledger: 119 closed records carry no
  reason and not one of them predates the field**, so every one is this defect rather than a
  record closed before the rule existed. The record said 109; the delta of 10 is this session's
  own closes going through the same seam and losing their reason each time.

  The create refusal reuses the pattern its sibling twelve lines away already had:
  `_close_drafts` raised on an argv naming no record, so the shape was in the same function
  group and was not being reused. A titleless record is a `created` event that states nothing,
  and `ledger_bodies` reads that event's *presence* rather than its content - so nothing
  downstream reports it, which is why the empty record survived.

  One agreement is pinned from the test side because nothing else can see both halves: the kit
  module the mirror is handed is `differential`, which exposes `events` and `migrate` and not
  `commands`, so the engine cannot read the kit's field name at runtime. A test loads
  `commands.py` by path and asserts the two are equal, the same route
  `labels.WRITER_LABELS` takes.

  `mirror.py` had 24 tokens of headroom and neither refusal was smaller, so the nine
  translations moved to `write_verbs.py` - 3976 to 845 and 3721. The seam was checked both ways
  before cutting. **The density waiver taken here is inverted from the six before it:**
  `mirror.py` did not get denser by gaining prose, it got denser by losing 3000 tokens of code,
  so the contract stayed and the denominator fell (basicly-5m2xfd, basicly-1qi0sz).

- A push no longer stashes the tracker ledger when it is the only unstaged change: `basicly hooks-build` now writes a ledger guard into the installed pre-push hook, so a hook killed mid-run can no longer drop ledger events appended while it ran. Unstaged files outside the ledger keep the previous behaviour. (basicly-6ajmrc)

- **A tracker write naming a record the ledger does not hold is refused, naming the id, instead
  of reported as recorded.** `basicly tracker write -- update <typo>` printed
  `recorded: update <typo>` and exited zero, on the one surface an operator uses to check their
  own work. Measured 2026-08-20 against a seeded ledger, the cost was worse than the report
  said. Only the flagless form wrote nothing at all; `update <typo> -t bug` **landed**, and the
  fold turned the mistyped id into a record no `create` ever minted, carrying whichever
  half-fact the argv stated. All five write verbs that reach the owned append — `close`, `comments add`,
  `dep add`, `gate report` and `update` — accepted an absent id and appended an event for it;
  `dep remove` was the only one that refused, because `basicly-he6200` had made it check the
  edge it was withdrawing. The append now reads the record set under the lock it is about to
  write through and refuses the whole batch, quoting the argv and the id it could not find.
  `create` is untouched and stays the exception: it mints its id in the same critical section
  and never comes through the append at all.

  Idempotence is unaffected, which is what makes this refusable at the seam rather than at each
  caller: a record's existence only ever moves one way, since a delete leaves a tombstone and
  the record stays in the fold, so no engine path that re-enters a state on every advance can
  meet the refusal on a later pass having got past it on the first. An edge's *target* is still
  unchecked — a dangling target is a different claim, and `merge` and `supervise` both add edges
  best-effort. One fixture relied on the old tolerance: `tests/test_gate_source.py` reported
  gates through the real seam against a record nothing had opened, and now opens it
  (basicly-6oypkd).

- **A lane a bound killed no longer reads as unmeterable.** A claude stream cut off before its result event is now metered off the per-turn usage it did report, as codex's always was, so one killed lane stops taking a granted session human-only. A halt that genuinely cannot be metered now names the dispatch and its model. (basicly-6y0tg5)

- A lane waiting for a process-budget slot is no longer flagged "may be stuck": the slot
  is granted before the stall watchdog starts, so the wait it measures is real work time. (basicly-7cdeyd)

- **The `work-tracker` skill no longer tells its reader that labelling a record is
  impossible.** The skill is projected into `.claude/skills/` and `.agents/skills/` and
  vendored by `basicly install`, so its prose is the instruction a dispatched agent follows,
  and three of its claims were false against the code in the same tree. Its refusals section
  said *there is no owned label write* and that *any instruction to label a record, a lane or
  a cut is therefore false*, while its own writes section showed the call working — and the
  call does work: the seam resolves `--add-label`/`--remove-label` against the record's own
  set under the ledger lock before translating, and the raise the bullet described is an inner
  guard on the un-resolved entry point that a user never reaches. An agent obeying the false
  half refuses `basicly loop supervise --label`, which is the multi-lane selection mechanism.
  The corrected bullet states the constraint that is real instead: a label write names exactly
  one record, so an `update` carrying a label flag and two ids is refused while every other
  `update` flag still applies to as many ids as the argv names.

  The second: the ready set was described as the records that are *open*, unblocked and not
  deferred. It is every record that is neither closed nor deferred, has no unclosed blocking
  dependency and has no children — **`in_progress` is in it**, because a claimed record is
  still the work, and a reader who believed otherwise would skip exactly the record a lane is
  holding. The third, that `create` without `--json` is refused, is gone. Nothing tests skill
  prose, so no gate saw any of the three (basicly-7wlhlp).

- `basicly install` validates `--technologies` before it writes anything, so a refused
  value can no longer leave a half-installed target behind. (basicly-859cqk)

- **The context window a dispatch is metered against is read off the adapter's own stream, so
  a consumer stops inheriting a stale constant.** `runner.py` shipped `claude: 200_000` while
  this repository's own `basicly.toml` raised it to 1_000_000, so a repo that installed the
  harness and did not hand-write `[runner.context_windows]` metered against the very figure
  whose staleness had put the finalize trigger at a fifth of its intended point here — lanes
  recorded occupancies up to 223_221 against a declared 200_000, and the override hid that
  from anyone measuring locally.

  **The remedy is not a bigger number.** Probed against claude 2.1.233 on 2026-08-15, a single
  dispatch reported *two* windows on its own stream — `claude-haiku-4-5` at 200_000 and
  `claude-opus-5[1m]` at 1_000_000 — so the window is a property of the model, not of the
  adapter, and no per-adapter constant can be right for both. `context_window.resolve` is now
  an order of preference: a window you declared wins, because the record has to explain the
  threshold the engine acted on; then the window the adapter reported for this dispatch,
  resolved by the model of the final turn rather than the first or the largest; then a dated
  shipped default; then a refusal. **`unmetered` is a real recorded answer**, not a fallback:
  codex and copilot report no window at all — established against positive controls, codex's
  `turn.completed` usage block is present and carries none, copilot's `modelMetrics` is
  present on 6 of 6 local stores and carries none — so neither ships a figure, and a dispatch
  on either records that it could not meter rather than assuming one.

  Every shipped default now carries the probe that read it and the day it was read, and
  `stale_declarations` fails a default that has neither, that disagrees with its own recorded
  probe, or that is past a 180-day re-read bound. That is a calendar falsifier: the existing
  one needed a lane to record a contradiction first, which means paying for it
  (basicly-89hm).

- **`basicly worktree cleanup` decides on content instead of ancestry, so it stops reporting
  every correctly landed worktree as unmerged.** The check was `git branch -d`, which answers
  ancestry, and ancestry is not what makes a branch safe to discard: a lane that queued behind
  another is replayed onto the base it finds, so its commits arrive under new shas and the
  original ref is not an ancestor even though base holds every line. Cleanup relayed git's
  `not fully merged` as *unmerged — re-run with force to reclaim*, and **a check that is wrong
  on every correct case teaches an operator to pass `--force` without reading it.** On
  2026-08-20 that habit came within one command of discarding a commit base genuinely did not
  hold. Worse, `git branch -d` also fails for reasons that have nothing to do with merging —
  a branch still checked out in a worktree gives `cannot delete branch … used by worktree`,
  and `-D` refuses that too, so the offered remedy could not have worked.

  Cleanup now compares content: the paths the branch changed since the fork point, against
  what base holds at those paths. Base holding all of them reclaims the session with no
  `--force`. **Anything else refuses and says which**, in four distinct sentences rather than
  one — base is missing named paths and force would discard them; the comparison could not be
  made; no session record names the base; git refused the delete outright. Only the paths the
  branch touched are compared, so a sibling lane's landings are not mistaken for missing work,
  which would be the same wrong-every-time answer pointing the other way. `git branch -d`
  stays as the fast path, so an ordinary merged branch still costs one git call, and
  `--force` keeps its old meaning: delete regardless, no question asked (basicly-8g719r).

- **The validator's verdict is read through the markdown an agent actually writes.**
  `validate_gate.verdict_from_reply` stripped whitespace and a pair of enclosing backticks
  and nothing else, so a reply reading `**VALIDATION: PASS**` parsed to no verdict at all —
  and an unreadable verdict costs the whole dispatch, not just the line. Markdown decoration
  is now removed anywhere on the line before the `VALIDATION:` prefix is matched, which
  covers emphasis around the whole line, emphasis around the label alone
  (`**VALIDATION:** PASS`, the shape that puts the markers *between* the prefix and the
  answer, where stripping the ends cannot reach), a heading prefix and a list marker. The
  forms come from agent-written text in this repo's own ledger; single `*` and `__` runs
  around a label line are extrapolated from the same convention and are marked as such at
  `validate_gate._MARKUP`.

  **The refusal is unchanged, and is now pinned.** Only `PASS` or `FAIL` after the prefix is
  a verdict, and only a line that says the prefix is a candidate, so a reply carrying no
  verdict still returns `None` and still queues the decision `basicly-xd79u3` added instead
  of the parse finding a verdict in anything. Two permissive mutations were run against the
  suite to prove that guard is still reachable: dropping the prefix anchor, and accepting any
  non-empty answer, each turn `tests/test_validate_gate.py` red. (basicly-8utmy8)

- The rework-divergence signature no longer reads a finding set that grew past the
  truncation limit as progress; a strict superset now compares as diverging. (basicly-95mp1k)

- **The validate decision queue kind has one spelling.** `loop._hold_for_validate_decision`
  passed the kind as a bare `"validate"` literal while `validate_gate.queue_unreadable_verdict`
  — the other site that queues one — named `validate_gate.VALIDATE_DECISION_KIND`. Both now
  name the symbol. No behaviour changes: the literal and the constant were the same string,
  which is exactly why nothing a call could observe would have caught them diverging.
  `decisions.enqueue` raises on a kind `decision_marker.KINDS` does not reserve, so a
  divergence would have failed the advance outright rather than mis-filing the item — the cost
  of a second spelling is that it is the one a later reader copies.
  `test_the_two_queue_sites_give_the_decision_kind_one_spelling` reads both function bodies
  and refuses one. (basicly-abv7v9)

- **A landed unit's cost record carries the forecast it was priced against, and counts the
  curator's dispatch.** Two defects in the `[harness-cost]` marker, measured over all 202 cost
  records in this repository's ledger on 2026-08-17. `forecast.tokens` was null in 185 of them
  and `scope_tokens` was null with it, so forecast against actual could not be computed for
  that population at all: the rollup looked the frozen estimate up by the record's *ownership
  scope* while the estimate had been priced over its *working set*, and a different glob set is
  a different key, so the lookup missed and returned a null rather than a forecast. The rollup
  now resolves through the same dispatch sizing the lane was priced by and falls back to the
  frozen forecast; when neither answers it records **the reason the forecast is absent** in a
  new `source` field instead of a bare null, and labels a forecast it computed itself `rollup`
  rather than borrowing the dispatch label, because an unfrozen resolution prices with today's
  factors and this runs after the merge.

  Second, the curator's dispatch was never in the total for the units that had one:
  `loop._on_ship` wrote the rollup and *then* dispatched curation, so the rollup preceded the
  spend it was meant to count. 8 of 202 units disagreed with the run records they hold and
  none over-counted — the worst reported one dispatch against three runs, 10.1% below two
  independent instruments that agreed on the real figure. The two calls are swapped. The
  rollup still precedes the tracker-state commit, because a marker written after it sits in
  the local store only, and that is the constraint the ordering had to keep (basicly-agzx.4).

- **A landing now states what it took: the branch tip and how many commits came with it.**
  `basicly worktree merge` and every landing behind it reported only the merge commit it
  produced — `merged harness/feat into main @ d605fb4` — which says nothing about the commits
  that were merged. On 2026-08-20 an agent finished, reported its commit, was resumed by a
  follow-up message, committed another 92 lines, and the landing took the moved tip; nothing
  in the output said the tip had moved, and the commit was recovered only because a
  `git diff <branch> main` was run by hand before cleanup. The report now reads
  `merged harness/feat @ 1a2b3c4d5e6f (3 commit(s)) into main @ d605fb4`, so the one
  irreversible step in the loop names the thing it consumed rather than only the thing it
  produced.

  The count is read **before** the merge, because afterwards it is zero for every branch, and
  a count git cannot answer is reported as `an uncounted number of commits` rather than as
  `0`: this exists so a landing can state what it took, and a number nothing measured is the
  same false report the change closes (basicly-aim1qi).

- **Every ledger write is attributed.** The event record carried an `actor` field and the
  live writer never populated it: empty on all 1,078 events written since the flip, against
  a positive control of 3,775 truthy actors across the whole log — every one of them from the
  import. A write now records the dispatched agent, or the operator masked, so "every state
  change is attributable" is a fact rather than a declared field (`basicly-at5tph`).

- **The board no longer goes backwards the moment a supervisor starts, and `IN FLIGHT` finally
  has a producer.** A live supervisor lock hands board production from `basicly board serve` to
  the supervisor's own heartbeat - the server stops folding and serves the supervisor's file
  instead - and the tick folded on the lock alone. Measured on this repository: with no
  supervisor the board carried a phase on 234 of 234 units, a ready set and `backlog.ready` /
  `backlog.blocked`; a supervised pass reverted every one of those to *not emitted by this
  producer*, and `IN FLIGHT` had never had a producer on that path at all.

  The tick now folds the same document `basicly board --out` folds, plus the in-flight lanes.
  The phase per record comes from `loop_state.phase_map` - one fold of the log for the whole
  population, measured at 84 ms over 1041 records, against the per-record read that priced the
  section out when the old reasoning was written - and each lane card reuses the view
  `loop session` already builds, so there is one answer to what a lane last ran rather than two.
  The lane selector the pass was started with rides along, so a `--label` pass draws its own
  lanes and not the root's children.

  A fact the tick genuinely cannot gather still leaves its section **absent** rather than
  zeroed: with no lock the `session` and `lanes` sections are omitted, and a session this
  checkout cannot derive publishes no lane list at all. A failed emission still costs one
  narrative line and never the pass.

  One emission measures 1.50 s on this repository, and 7.11 s - 47% of the 15 s beat - once
  run records exist, because the grant-spend walk behind `session.spent_tokens` costs 5.9 s of
  that. It runs after the heartbeat write, so it delays the next beat and never a landing, and
  clears the 60 s staleness horizon by 8x (`basicly-bd4epr`).

- **A release commit no longer arrives at its own hook stale.** The version bump adds a
  character to every projected header, and the `always-on-sizes` block states those sizes, so
  the pre-commit fixer rewrote `architecture.md` mid-commit and the framework refused the
  release. `basicly release` now applies the fast fixers after it rebuilds (basicly-cmc998).

- **The board snapshot schema no longer warns readers off an edit that is safe.** Its
  first line claimed `wired-or-deleted` indexes the file's prose as field references;
  `basicly-r343` had already narrowed that scan to object keys plus the string values
  under `required`, `enum`, `const` and `$ref`, so a `description` is never read. The
  line now states what the gate does read, which makes the real hazard — a new key or
  permitted value repeating a declared name — the one a reader is warned about
  (`basicly-desr1v`).

- **`basicly health` no longer scores a lane our own spend ceiling stopped as an agent
  failure.** A run carrying `stopped_bound` leaves the failure rate and is counted under
  its bound instead, on the whole-history score and inside each drift window — the same
  population rule `decompose.unsized_lane_tokens` and `spend_accuracy` already apply. On
  this repo's ledger that moves claude from 29 failures over 163 dispatches (18%) to 20
  over 154 (13%), with the other 9 reported as `stopped by a bound (spend 9)`, and it
  retires the `REGRESSED claude` flag the supervisor pass line raised over four lanes the
  grant ceiling halted 96ms apart on 2026-08-13. The health payload is `schema_version`
  2: `failed` now names a narrower population. The ceiling is unchanged, and the report
  is still observability — it never refuses a lane (`basicly-e2mz.3`).

- **A tracker `update` through the engine seam writes every field the store holds under its own
  key, instead of refusing all but three.** The translator was three flags wide — `-t`,
  `--type` and `--external-ref` — so a write of a description or of acceptance criteria was
  refused outright, and a record filed through the seam could carry a type and an external ref
  and nothing else. The refusal itself was right, because dropping the field silently is the
  divergence that layer exists to prevent; the flag table was the defect. Fifteen flags now
  translate, naming ten fields — title, description, design, acceptance criteria, notes, type,
  priority, assignee, owner and external ref — with `--body`, `--acceptance` and `-d` taken as
  the aliases the store itself accepts. The field *names* matter because the folded record
  renders them straight back, and the plan gate reads `acceptance_criteria` off that record.

  Priority goes through a converter rather than `int`, so `-p P1`, `-p p1` and `-p 1` are one
  priority and the ledger holds the integer; the same table serves `create`, which used to
  crash on `-p P1` with a bare `ValueError`. Four flag families stay refused, each for a
  measured reason, and the message now states the precondition **before** naming the repair so
  that following it cannot turn an append into a replace: the label flags accumulate against
  the set a record already holds rather than replacing it, `--claim` carries no value, `--due`
  and `--defer` are re-based against the host clock, and `--estimate` lands under a field no
  record here holds (basicly-e2mz.30).

- **A lane is no longer refused at landing for a generated file it is fenced out of repairing.**
  A lane whose diff only *adds* a file leaves a declared-regenerable block counting a tree that
  no longer exists, and the documents those blocks live in are outside every lane's scope by
  design — so the landing verify failed the lane for a defect it may not touch, and the lane
  spent its whole rework budget discovering that. Two lanes escalated on it in one day while a
  third, which modified files and added none, landed cleanly on the same base at the same
  moment.

  `rebase.refresh_generated` now runs every command declared in
  `[worktree.regenerate_commands]` against the rebased worktree before the landing verify and
  commits what changed. Committed rather than left in the tree, because the landing merges the
  branch and an uncommitted rebuild would pass verify and never reach the base. The existing
  rebuild fired only on a merge *conflict* confined to those paths; staleness needs no
  conflict, because the rebase changed the tree the artifact is derived from, and the two are
  the same class.

  When regeneration does not make the file current, the landing **names the path and the
  command that rebuilds it** instead of reporting a plain verify failure. It reads that out of
  the failing check's own captured output rather than rebuilding a second time to find out,
  because probing by rebuild would dirty the lane's worktree on the way to refusing it, and it
  reads the same declared map that `loop preflight` prints, not a second list beside it
  (basicly-e2mz.35).

- **The prose-share instrument refuses source it cannot parse, instead of reporting it as
  0% prose.** `check_comment_density.prose_tokens` returned 0 on any fragment that did not
  `ast.parse`, and documented the 0 in its own docstring. That is the most dangerous answer
  the function had available, because the two size ratchets pull opposite ways: an extraction
  is safe only when the extracted unit is prose-**heavier** than the module it leaves, so a
  lane measuring a docstring section or a method lifted out of its class was told the exact
  opposite of the truth, every time. Measured 2026-08-20, a lane derived 66% by a second path
  sharing no step with the first, against this 0, and only then knew its first measurement was
  an instrument fault rather than a result. It now raises `RatchetError` naming the reason and
  the remedy - parse an extracted unit as the module it will become, not as a raw slice.

  The whole-tree path keeps the tolerance it had, and keeps it for the reason the old test
  gave: a tracked module with a syntax error is ruff's finding to report, and comment-density
  adding a second failure for one cause helps nobody. `verify --mode full` runs every check
  rather than stopping at the first, and ruff runs before this gate, so the tolerance is about
  the report and not about the gate's ability to run. The refusal is for the other caller, a
  lane measuring a fragment, which has no ruff run standing behind it. Both halves are pinned
  by a test that was shown to fail when its half is reverted.

  One test module had to move for this to land: `tests/test_check_comment_density.py` had
  reached the 4000-token cap, and the waiver the gate offers as a remedy would have failed the
  module's own `test_neither_the_gate_nor_this_test_carries_a_waiver`. The `basicly.d` fragment
  delta route is now `tests/test_check_comment_density_fragments.py` - five helpers and five
  tests, all of the subprocess half - and the boundary is the fragment route against
  measurement and ratchet decisions. The split carries the sibling's no-waiver assertion into
  the new file, which the original could no longer make on its behalf (basicly-e7rtjn).

- **A wall row now names the feature it implements.** The operator's report was that
  `P1  basicly-a4q3.10  Carry a ranked kill list and its discriminator on a change summary`
  carries a priority, an id and a title, and nothing saying which feature it serves. The
  fact was already on the wire, so the ready set is now grouped under the epic or feature
  each row resolves to and no producer field was added: the parent edges are the `graph`
  section and the titles are the `units` section, both folded from the document the tick
  already carries.

  Rows group under the **root** ancestor rather than the immediate parent, because the epic
  is the name a reader recognises. Verified against the live document and not only against
  the fixture, whose graph carries `blocks` edges and no `parent-child` edge at all: every
  heading on the page is the title of that row's root in `graph.edges`, with
  `basicly-0hxck3` resolving two levels up to `basicly-k6tpep`.

  A heading counts the **whole ready set**, not the rows drawn beneath it. The unattached
  heading reads 41 while six of its rows are drawn, and 41 is the orphan count derived
  independently over the same snapshot — a quarter of the ready set attached to no feature
  at all, which is the second finding the grouping makes visible and which a slice-derived
  count would have hidden.

  Two defects were found by exercising the change rather than by reading it. A unit that
  merely *feeds* a cycle took the title of whichever member the walk halted on, filing it
  under a feature the graph never claimed; the regression test was run against the pre-fix
  walk to confirm that only that case discriminates, the two obvious cycle shapes passing
  either way. And a heading is a drawn line, so it now spends a slot: six headings over
  fourteen rows ran the ready region 137px past its box at 1440x900, which the same document
  rendered through the previous template does not do. (basicly-eaw1dy)

- **The harness board design no longer states a snapshot build time its own table refutes.**
  Constraint C5 claimed 19.1 ms for a whole build and a 26x margin against the 500 ms
  acceptance cap, while the per-source table directly above it listed 16.5 ms for the fold
  alone - the figure had excluded the log read, which is the largest single cost in the
  producer. Re-measured on the tree that ships `units` and `graph`: **103.8 ms**, median of
  21, decomposed step by step so the whole can be checked against its parts. The reduction
  against `observe()` is 59x rather than 320x, and the real headroom against the cap is
  4.8x, which is recorded as a band rather than a loose bound (`basicly-ef953m`).

- **The board snapshot now carries the fields a wall board is read for: a loop phase and a
  readiness flag per unit, `ready`/`blocked` on `backlog`, the branch, head and dirty state of
  the checkout, the age of every pending ask and the question behind it, and the run's grant
  level, token budget and spend.** Measured on this repository before the fix, `phase` was null
  on 233 of 233 units, `ready` was 0 on 233 of 233, `lanes` and `session` were absent, an ask
  carried neither a question nor a waiting time, and `repo` was a name. Every panel a person
  reads was therefore empty or a count.

  **The fix is the caller, not the section.** `board_sections.units` was right to omit `phase`
  and `ready`: the first is `loop_state.derive_phase` reading a required-gate set the file-only
  producer does not open, and the second is the tracker's own walk over a status vocabulary and
  the whole edge population. A second spelling of either inside a display producer is how two
  derivations come to disagree, and the schema has no field marking a value as derived, so a
  guess would render identically to a read. So they join the lane facts and the lock facts on
  `board_snapshot.Facts`, and whatever a caller withholds stays **absent** - `basicly board`
  supplies them, and `tests/test_board_snapshot.py` pins both directions.

  `repo.branch`/`head`/`dirty` travel the same way for a different reason: `dirty` is
  `git status` and the producer spawns no subprocess, which a spy in
  `tests/test_board_snapshot.py` pins. `asks[].waiting_s` is the one value derived in the
  producer, and it is arithmetic on the injected `now` the document is already dated with
  rather than a clock reading - the shape `board_render`'s freshness age was already exempted
  for. `asks[].question` cannot be derived at all: `policy.record_wait_request` writes an id, a
  kind and the word `requested`, so the wording exists only on the decision queue and is paired
  back to its wait on the checkpoint name appearing in the question, which is
  `decisions.settle_checkpoint`'s own rule.

  **Two facts are bounded by cost, and the bound is published rather than hidden.**
  `loop_state.read_node_state` is the only route to `derive_phase` and it reads the whole event
  log seven times per record - 591 ms over 20 records on this repo's log - so a phase for all
  234 active records is 138 s against a 171 ms build. `basicly board` derived phases for the
  ranked ready front only, and every unit outside it kept `phase` absent; `basicly-s1vqq2`
  removed that cap. `session.spent_tokens` sits behind `policy.session_issue_ids` at 13.1 s and
  behind a run-record file this checkout may not have, so it is emitted only where both hold:
  the figure
  is spend *under the active grant*, never the lifetime one, because publishing the lifetime
  figure beside a ceiling is how a display comes to draw 177970761/4000000 with nothing spent
  under that grant (`basicly-f3tked`).

- **The board's reclaimed ready list now fits the actual wall.** `basicly board --out` drew a
  fixed 14 rows even on a screen 26 fit, because the cap ignored the viewport. `--height` and
  `--width` let an operator state the wall's own size; the row count is measured from it and
  stays at the old safe default when neither is given. (basicly-ffm2yp)

- **A gate that refuses a harness git command now names the check and the reason that check
  printed, instead of the argv and the exit code.** Three code paths reported a hook refusal on
  2026-08-21 and none of them named a check. `commit.salvage` reported the *last line of the hook
  chain*, which belongs to whichever hook ran last - in this repository
  `protect-generated-commit`, which had passed - so the one message a reader got pointed at a
  check that did not fail. Two lane closes printed `command failed (1): git commit -m ...` because
  `checkout.run` read `stderr or stdout`: pre-commit writes the whole chain to stdout while `uv`
  writes a `VIRTUAL_ENV` warning to stderr on every run in this tree, so the report was discarded
  and the warning was the entire diagnosis.

  Both streams are now joined and read by structure. A failing hook is located by its verdict
  line, and its reason is taken from *its own* block, so a passing hook's line can no longer be
  quoted as a rejection. Within that block the reason is chosen by what a line claims rather than
  by where it sits: the first design took the block's tail, and real output refuted it, because
  this repository's `pre-commit-script` hook wraps the whole verify suite and its block ends on a
  list of the checks it ran while the answer - `checks failed: 28/32 passed ... (failed: ...)` -
  sits six lines earlier. A failure with no hook chain in it keeps the old wording, since
  `git rev-parse` has no check to name.

  A landing that fails `release-notes` now carries the remedy that gate already printed - the
  exact `changelog.d/<id>.<category>.md` to write - which was being captured and thrown away. When
  a chain did run but names no failing check, the message says so and names
  `.basicly/usage/gate-output.txt`, where the full output is written, rather than implying a cause
  it cannot support. (basicly-fi1i7z)

- **Two live code citations now point at documents that exist and sections that define what
  the citing line claims.** Both were inside the `code-citations` gate's frozen debt on the
  day it landed, so they were recorded rather than blocking, and both were real stale
  pointers. `tests/test_policy.py` cited `gates-and-rework-design.md` sections 1 and 2 — the
  gate taxonomy and the rule that a pre-flight gate is read-only — and **that document was
  absorbed and deleted on 2026-08-08**, which no reader would notice without trying to open
  it. Its content went to `factory-loop.md` §5.1, which says so in its own first line, and
  `policy.preflight_gate` already cited that section for the same rule; the test now cites it
  too. The second citation named §1.1 and §4.1, whose mappings landed in the same section —
  and the rubric split that §4.1 argued has no document of its own, which the docstring now
  states rather than implying a section number for it.

  The shipped tracker kit's `events.py` cited sections 32.10, 32.3 and 32.3.2 while its module
  header binds the kit to `work-tracker.md`, whose highest section is 16: it named one document
  and meant another, which is exactly the ambiguity the gate exists to expose. Those three are
  **architecture** sections — the per-event size cap, the event vocabulary, and the reader's
  alias table — and each citing line now names `architecture.md`. Naming the document per line
  is the repair rather than a path binding, which would have re-attributed every one of that
  module's bare marks to the architecture when most of them really do mean `work-tracker.md`.

  Neither reference was deleted to pass: an unresolved mark is a pointer whose target moved,
  and the pointer is the evidence of what the code was reasoning about. Both modules reached
  zero unresolved marks and **both frozen entries were deleted in the same diff**, so the
  closed list now refuses a single new one in either file (basicly-fsuhg3).

- **The board snapshot schema no longer publishes a spend figure nothing here can re-derive.**
  The `spend` description stated 953.82 USD over 357 dispatches, measured 2026-08-14. Not the
  deleted store's number, so it fell outside the record that repaired those - and not
  re-derivable at all: `.basicly/usage/` is git-ignored and **nothing under it is tracked by
  git**, so no gate could ever notice the figure drifting. Proved rather than asserted, and the
  proof produced a third number: the design document says 431 dispatches, the schema said 357,
  and this checkout holds 321 records. One quantity, three figures, none checkable, changing
  per machine and per moment. The description now says what the field is and why it carries no
  figure, and points at `docs/requirements/harness-board.md` where the motivating measurement
  is dated and attributed.

  **The record's first criterion sent me looking for the others, and they were stale too.** A
  scan of every description in the schema found five more measurement-shaped spans. Those are a
  different case - the ledger *is* tracked, so they are checkable - and they had drifted: the
  event log read 5,890,340 B against an actual 6,396,125 B, **+8.6% in six days**. Re-derived
  and re-dated rather than removed, because a derivable figure satisfies the criterion's first
  branch. The field-selection ratio moved 132.5x to **156.2x**, so the claim was stale in the
  direction that made the argument weaker than the truth, and both figures now carry the date
  they were derived on plus the previous reading, so the next drift is visible as a delta
  rather than as a surprise.

  One correction along the way, and it is the same shape as the defect: my first count of the
  active rows said 237 against the schema's 236, because I counted `snapshot.jsonl`'s header
  line as a record. The schema was right and my instrument was wrong (basicly-fxdrcf).

- **The pipe-status guard pairs a `$?` with the pipeline it actually terminates, instead of
  with any pipe anywhere in the same invocation.** `$?` holds the status of the command
  immediately before it, so a command running in between claims it - but the guard scanned
  every segment after a filter and fired if a `$?` appeared in any of them. The refused shape
  was therefore the ordinary multi-step block: run a filter, then run a redirected gate, then
  read *that* gate's status. Worse, the guard's own advice text recommends the redirect half of
  exactly that block, so the check refused the habit it was installed to teach.

  Two commands were refused verbatim while this fix was being written, and both are now
  fixtures rather than paraphrases: `... | head -5; <gate> > out.txt 2>&1; echo "exit=$?"` and
  `sed ... | head -20; npx markdownlint ...; echo $?`. The earlier session's commands stay
  unreconstructed for the reason the test module already gives - a guard written against a
  paraphrase is a guard against the paraphrase.

  **One refusal recorded as a false positive was not one, and the record now says so.** A
  `jq ... | sed ... | sort > out.tsv && echo done` was refused for `sort`, and that is correct:
  `&&` branches on the pipeline's status, which is `sort`'s, so a failing `jq` upstream leaves
  the chain proceeding on a lie. It is pinned as a true positive with the redirect sitting
  between the filter and the operator, because that is where a parser would plausibly lose it.
  The measured tally on the record was corrected from 5 false against 1 true down to the cases
  a verbatim command can be re-run for.

  The five fire conditions are otherwise unchanged, each shown to fail its own test when
  reverted: `$?` immediately after, `&&`/`||` after, an `if`/`while`/`until` condition, and
  `run_in_background`. Both directions were also exercised against the live hook rather than
  only in tests: the false-positive shape now runs, and `<gate> | tail -2; echo $?` is still
  refused (basicly-g8jxj3).

- **The board's lane card no longer clips the model id, and the page no longer leaves most of
  its height black.** Both were one CSS decision. `board_page.html.j2` declared a grid whose
  right column was a fixed `470px` while the running row was `minmax(0, 1fr)`, so at 1920x2400
  roughly 70% of the page was black and `claude · claude-opu…` clipped at *every* width - a
  lane card was about 390px inside that column, and `_lane_cells` puts the agent and the model
  in one cell, so the model id had no room to have.

  The fixed column is gone and the page flows. A full-width identity line at 1280 is about
  590px of text in a 1248px box, so the model id reads with roughly two times headroom, and at
  1600 two cards per row still leave about 760px each.

  The card also gains the field with no substitute - the line saying what a lane is doing,
  which is the only thing that tells a working lane from a wedged one - and drops the rows it
  had nothing to put in. The fixed-height slot arithmetic retires with the fixed layout:
  `READY_SLOTS`, `READY_SLOTS_WIDE` and `BAND_ASKS` existed to promise a rendered height
  against a fixed viewport, which a page that flows does not need, and that is what lets the
  alarm band show more than one waiting ask. (basicly-gnpgf8)

- **The worktree concurrency cap counts checkouts instead of records, so a session whose
  directory is gone stops holding a slot.** Both routes to a record outliving its checkout
  were hit on 2026-08-20: `basicly worktree cleanup` without `--force` keeps the record when
  the branch survives, and a plain `git worktree remove` tells the engine nothing at all. The
  refusal then read `worktree concurrency cap reached (5/5)` with **three** worktrees on disk,
  and `basicly worktree list` marked the other two `(stale: dir missing)` while they went on
  blocking every provision. A stale record occupies no checkout and contends for no gate, so
  it now counts for nothing.

  **The refusal also names what to reclaim.** The old message named only the cap, which makes
  raising the cap the cheapest reading — and that is what an operator did instead of freeing a
  slot. It now reads `cap reached (1/1 live)` followed by the records whose checkout is
  already gone and the `basicly worktree cleanup <name> --force` that clears them. That
  message had been hand-written separately at both places the cap is evaluated, `basicly
  worktree create` and the loop's build advance; both now compose it from one place, so the
  two cannot drift again and neither can disagree with the count (basicly-gtoqu9).

- **A `change-summary` artifact records the commit the landing actually took.** The head
  was read before the merge, which rebases the branch and can add a regeneration commit on
  top of it, so the recorded `commit` named a sha that no longer existed: `basicly-gvlpxm`'s
  own summary carried `d3422f81` while its branch stood two commits later at `634c125a`,
  and the changed-path count and digest beside it described that stale tree. The head now
  comes back from the landing itself, so it resolves in the base branch and the paths a
  reader derives from it are the ones that landed. The changed-path set is still read
  before the merge, where it is still the build's own (`basicly-gvlpxm`).

- **The architecture layering section's tier and band counts are generated from the import
  contract.** Section 34 stated how many tiers the engine has and how many modules they hold,
  and **nothing read any of it** — no script, no test; `docs-claims` asserted CLI coverage
  only, and `code-citations` checks that a citation reaches a heading, not that a number
  inside a document matches a config file. Measured against `.importlinter` on 2026-08-20 the
  document said 36 tiers where the contract had 37, and its band labels summed to 98 modules
  where the contract had 102. A previous lane corrected the three numbers its own change
  moved and deliberately left two band figures, because band boundaries are read off a
  diagram nothing binds — so correcting them could itself be wrong.

  Correcting a number is the repair that is wrong again on the next tier, so the whole block
  is now a `docs-claims` generated block over `.importlinter`: the tier count, the module
  count, every band's module count and the diagram's declared-exemption edges are all derived,
  and a tier added to the contract fails the gate until the block is regenerated. **The band
  boundaries are the declared half and the block says so where a reader meets the counts.**
  Nine bands over 38 tiers is an editorial reading the contract does not carry, so each
  boundary and each band's example modules are declared in `.scripts/docs_claim_layers.py` and
  the counts are derived against them — a boundary the contract no longer declares, an example
  module that moved band, or a tier below the bottom band all raise rather than render, because
  a band count nobody can derive printed as though it were derived is worse than a wrong one.

  The two figures the previous lane left are now derived rather than guessed: band 7 holds 13
  modules and band 9 holds 26, and the labels sum to the contract's 104 — cross-derived
  against the package's 103 top-level modules plus the `renderers` package (basicly-h7bknm).

- The release-notes gate no longer refuses a commit in a checkout that merely predates a record's note. A fragment the base branch holds and the branch point did not is now reported as a rebase to make, not as debt to pay, so a lane seeded before a record closed can still commit. (basicly-h8dxhy)

- **A tracker write that translates to no event at all is refused, instead of reported as
  `recorded:`.** `cmd_write` printed its confirmation from the fact that no exception had been
  raised rather than from what landed, and `mirror._update_drafts` returns an empty draft list
  when an `update` carries no field flag. So `basicly tracker write -- update <id>` printed
  `recorded: update <id>`, exited zero, and appended nothing - identically whether or not the
  record existed, so the message did not discriminate either. Verified both ways.

  **The refusal went one level up from the verb, and that is the point.** The record scoped it to
  `mirror._update_drafts`; it lives in `owned_write.append`, which sees the drafts every one of
  the seven translated verbs produces. The defect is the shape rather than the verb - any
  translation yielding nothing is a confirmation about nothing - and `basicly-vkh0.50` already
  owns that general claim. `init` and `sync` are exempt by construction: `mirror.UNMIRRORED_WRITES`
  is the set of writes that legitimately state nothing about a record, and it is the same set the
  untranslatable-write refusal already names, so it went from private to public rather than being
  respelled.

  The order of the two refusals is forced rather than chosen. `refuse_a_write_to_an_absent_record`
  returns early on an empty draft list, by design, so it cannot speak for a flagless write at all;
  the records-nothing check has to run first. A flagless update naming an id nothing holds
  therefore reports what it would have changed rather than that the id is unknown. A flagged
  update naming that id still gets the absent-record message, which is the case where the id is
  the thing to fix.

  This also corrects `basicly-6oypkd`'s premise, which that record already states: its
  "nothing reaches the ledger for an absent record" was verified with a flagless probe, so the
  zero came from this defect and not from the one being reported. Both halves are pinned by a
  test shown to fail when its half is reverted, and the record's four-step reproduction was run
  against the live CLI: flagless refuses, flagless on an unknown id refuses, and `update <id> -p 1`
  still prints `recorded:` (basicly-holhk4).

- **`basicly worktree create` records the binding it just earned.** The verb provisioned the
  tree, the branch, the dependencies and the hooks and wrote no worktree binding, so
  `loop_state.derive_phase` read the record as `intake` and no advance could land a merge that
  had already happened. Three records in one session were closed by hand for it, each carrying
  a prose close reason where a `release-record` artifact should be (`basicly-i8urje`).

- The board's own footer cells (spend, gates, agents, dep edges, events, and the "not
  drawn" roster) no longer spell an absent section as `not emitted by this producer` or a
  withheld one as the bare word `withheld`; a withheld section now shows the schema's own
  reason instead. (basicly-jbd80w)

- The dispatch brief now names the skills a unit's own work declares (`covers:` on a
  skill source, refused by `basicly catalog lint` when the engine cannot dispatch it),
  and `basicly usage report` splits never-used skills into *delivered by a dispatch,
  never self-invoked* and *unreachable* instead of calling them culling candidates. (basicly-jcl4rm)

- **Planning guidance now says how to find the files a fix touches, not only how to declare
  them honestly.** A scope is a claim about where a wrong value is *produced*, and the
  tempting answer is where it is *displayed*. Those are usually different modules, and the
  gap tends to be discovered only after an agent has spent a budget reaching it.

  Four scopes were written wrong in a single session, all the same way: one named the loop
  and the gate runner when the producers were the three modules that actually run `git`; two
  named a renderer when the rows are built one call below it; one named two projected skill
  surfaces when skills project to three. Three of the four were caught downstream - one at
  the landing gate, which routed a correct and verified change to rework for touching eleven
  files outside its declared four, and one by the projection check refusing the commit. Only
  the two caught by probing before dispatch were cheap.

  `decompose-plan` gains **Locate the producer, never the surface**, carrying those four
  instances, the probe as a runnable command rather than an instruction to think harder, and
  the follow-up question that catches the projection case: does this value reach more than
  one surface? A projected artifact usually has several, and a renderer almost always sits
  one call above the builder that owns the fact.

  The existing honest-sizing section is untouched. The two answer different questions: that
  one is about not shrinking a scope you already know, this one is about naming a confidently
  wrong one. (basicly-k87ec4)

- **The release commit no longer refuses itself over the notes it publishes.** Assembly deleted
  the fragment filename that accounted for a record, so 43 of 149 records lost their note in the
  commit publishing it. The assembler now writes `(<record-id>)` onto a fragment whose body lacks
  it (basicly-k8b75o).

- Concurrent confirm-code mints and consumes no longer lose each other's writes: the store's read-modify-write is serialised across processes, so two checkpoint approvals racing each other keep both codes. (basicly-kas8q7)

- **`basicly loop supervise` seeds lanes from a root whose decompose checkpoint a live grant
  delegates, instead of refusing and sending the operator back to `loop run` per child.**
  A root with children derives `decompose` (`loop_state.derive_phase`), and its decompose
  checkpoint gates the fan-out — so `loop supervise <epic>` answered `seed-blocked - no lane
  could be provisioned from 12 open child(ren) - decompose checkpoint awaiting human approval`
  and exited non-zero, under a live L3 grant that `policy.GRANT_COVERAGE` delegates exactly
  that checkpoint to. The cause was the driver: seeding used `loop.run_until_blocked`, which
  stops dead at a checkpoint and never reaches `policy.approve_checkpoint_guarded`, so no
  grant was consulted at any point on the seeding path. The operator then hand-drove
  `loop run` once per child, on the same root and the same grant, and every one delegated.

  Seeding now drives `loop.run_ceremony`, the same command `basicly loop run` is built on,
  naming the session's own root as the grant root. **Nothing is widened by the swap**: the
  ceremony's only route to an approval is that same guarded predicate, so a checkpoint no
  grant covers still stops the pass — and it now says which of the three things happened.
  A refusal names the level that *would* delegate the checkpoint and the command to issue
  one; a covering grant that declined repeats its own reason; a rejected confirm code reads
  as a refusal. "Awaiting human approval" said none of those, which is why an operator
  holding a covering grant could not tell it had never been asked.

  `basicly loop preflight` stops calling that checkpoint a blocker when the live grant
  delegates it, and prints the delegate-it remedy beside the approve-it one when nothing
  does. Preflight refusing a pass that now runs would be the same defect inverted
  (basicly-kjc5.62).

- **Concurrent lane dispatch no longer loses three lanes out of four to the base-checkout
  commit, and a lane that queues for it is told so in those words.** Every `basicly loop run`
  publishes its claim by committing tracker state in the *base* checkout before it provisions
  a worktree — one index and one HEAD, shared by every dispatch, and nothing guarded it.
  Observed 2026-08-19: four dispatches started in the same second, one committed and three
  exited non-zero having done nothing, two on `git commit`'s exit 1 (a peer had already
  committed the same dirt, so nothing was staged) and one on exit 128 (a peer held
  `.git/index.lock`). So the factory's fan-out width was bounded by an unguarded serial step
  rather than by the isolation model it advertises. Reproduced against the pre-fix code path
  with the interleaving injected rather than raced: three of four dispatches failed, with
  exactly the message the incident recorded.

  `merge.commit_tracker_state` is the single funnel every one of those dispatches goes through,
  and it now holds a file lock (`basicly.base_lock`) across the whole read-then-commit window.
  A loser **waits** for the holder instead of racing it, and because the status is read inside
  the lock a loser finds the tree its peer left — its own claim already published — so it
  declines rather than recording the claim twice. A dispatch still queued after the budget
  fails with a message that names *contention*, the holding pid and how long it waited, because
  half of this defect was that `Error: command failed (1): git commit` reads as a rejected
  commit and sent an operator into the hook chain.

  **Stated failure mode:** liveness is the lock file's mtime and nothing refreshes it, since
  the critical section is one gated `git commit` with no thread to beat from. A commit slower
  than the hold budget is declared crashed and its lock taken over — which costs that one lane
  the pre-fix behaviour, loudly, on work it has not started yet (basicly-kjc5.63).

- **The landing scope gate no longer faults a lane for the two files every lane writes.**
  This repo's conventions have each lane record its ratchet delta as `basicly.d/<id>.toml` and
  its release note as `changelog.d/<id>.<category>.md`, so neither appears in any bead's
  `## Scope` and `loop._scope_block` reported both as out-of-scope edits. Observed on
  `basicly-gvlpxm`: the two false entries arrived in the same message as one genuine
  collision, under a closing line that offers `[policy] scope_collision = "warn"` as the way
  to land — so the noise argued for turning the gate off.

  Both are now in scope by construction, derived from the record id the engine already holds
  (`config.lane_scope`). Derived and not a directory whitelist, which is the whole point:
  `basicly.d/<other-id>.toml` is a real collision and this gate is still the only thing that
  sees it, while `README.md` in either directory names no record and stays undeclared
  (`basicly-kjc5.64`).

- **An artifact recorded against a record the ledger does not hold is refused, naming the id,
  instead of appended.** `basicly-6oypkd` landed the absent-record guard in
  `owned_write.append`, which every argv-shaped write reaches. `tracker.add_artifact` does not
  reach it: it hands the ledger an object rather than an argv, precisely so a JSON body is not
  flattened into the free text the per-event cap cuts. So the guard covered six verbs and one
  surface sat outside it, which is the shape where a control reads as complete and is not. The
  guard is now one definition with two callers rather than a copy, and `add_artifact` holds the
  ledger lock across the check and the append for the reason `append` already did: the record
  set a write is refused against has to be the set the append lands on.

  **The consequence was narrower than the record claimed, and the correction is worth having.**
  The record said an artifact against a mistyped id is "evidence attached to nothing". Measured:
  the fold *does* mint the id, as a record with no `created` event, so `ledger-bodies` reports it
  at the next commit. The hole was covered downstream. Refusing at the seam is still the right
  place, because an append-only log has no undelete and the event is already written by the time
  that gate speaks - but a reader should know a gate would have shouted.

  **26 existing tests depended on the tolerance**, across three modules, every one writing an
  artifact against a record its fixture never created. Each is fixed by opening the record, the
  way `basicly-6oypkd` fixed the same shape in `test_gate_source._repo`, rather than by loosening
  an assertion. The two autouse fixtures key off `request.fixturenames` instead of taking
  `work_repo`, so the tracked-tree copy is not built for the tests that only want `tmp_path`.

  **One module had to give up tests for this to land, and the reason is the finding.**
  `tests/test_handoff.py` needed 190 tokens and was frozen at 6302 having already been
  rebaselined three times - 3986 to 4134 to 5124 to 6302, +58% - with all three fragments giving
  the same reason and deferring the same extraction. That extraction has since landed:
  `cut_violation` lives in `artifact_record`. The test-side home they named,
  `test_handoff_schemas.py`, has the room and is the wrong responsibility: it validates schema
  files, takes no repo fixture and never calls `handoff.record`. The corrupted-artifact section
  moved instead to `test_handoff_states.py`, where the entry-refusal tests it joins already live.
  `test_handoff.py` fell to 5784 for the first time across those three concessions
  (basicly-kmqno2).

- **A write the ledger already held no longer reports `recorded:`.** An event id is a digest
  over the fact, so a fact the ledger already holds is skipped as an idempotent replay - but
  `basicly tracker write` printed `recorded:` from no exception having been raised rather than
  from what landed, so the skip read as success. `--add-label live-demo`, then
  `--remove-label live-demo`, then the same add again confirmed a label write three times over
  a field that never moved, and the third confirmation is what bought a wrong diagnosis. The
  seam now says `already recorded, so nothing was appended` and adds that the record still
  reads as it did.

  The swallow itself is deliberately unchanged, so a genuine duplicate replay still appends
  once and states nothing new. **Saying a re-record is meant is still not possible:** driving
  one field to A, to B, and back to A leaves it at B, because the history `A, B` and a
  deliberate re-record of `A` after `B` leave the ledger byte-identical and no rule reading it
  can separate them. The intent has to come from the caller, which is what `Draft.generation`
  is for and what no write verb yet reaches (`basicly-z9bggw`) (basicly-kn4rip).

- `basicly board serve` no longer serves a stale model against a fresh template: a
  long-lived server re-reads its inputs per fold instead of blanking a region in silence. (basicly-mcf2uh)

- The session walk behind `basicly board` and the grant spend meter reads the tracker ledger once per walk instead of once per bead in the session: an 87-bead session cost 8.77 s over 87 folds of the whole log and now costs 0.20 s. Same ids, same figures. (basicly-mdv1qu)

- **A ratchet gate can be rebaselined by the route its own remedy prescribes.** `[ratchet]` in
  `CONFIG_SCHEMA` named three gates while five call `ratchet.compose_ratchet`, so a lane that
  followed `code-citations`' or `release-notes`' printed remedy and wrote
  `[ratchet.code_citations]` into its `basicly.d` fragment got `unknown section
  'code_citations' in [ratchet]` from every command that reads the config — 166 tests the
  moment one did. Both gates shipped green because nothing exercised their rebaseline route:
  the section they compose from and the section the schema accepts were declared in different
  files and agreed only by review.

  The two missing names are registered, and the agreement is now derived rather than reviewed.
  `test_ratchet_sections_register_every_gate_that_composes_one` parses every `.py` under
  `.scripts/` and `src/basicly/` for a `compose_ratchet` call, resolves each call's gate
  argument through the module-level constant the caller spells it as, and fails naming the gate,
  the file that composes it and the declaration `src/basicly/config.py` is missing — so a sixth
  gate cannot land without its section. The walk asserts it has found the three long-registered
  gates before it reports a difference, because a probe that found nothing would pass for free,
  and it excludes `tests/` so a suite's fixture gate name is not demanded of the schema
  (basicly-nlouqg).

- **A ratchet fragment can record the commit its measurements were taken at, and is refused
  where that commit is not in the head's history.** A `basicly.d` delta composes in any order,
  which is what the directory is for; the *headroom* a lane measures before choosing that delta
  does not. Two lanes branched from one commit both measured `src/basicly/merge.py` at exactly
  2 tokens of module-size headroom, each declared a rebaseline that fitted and spent that same
  2, and the composed tree came out 2 over — green on both branches, red only on the merge, and
  the operator saw a two-token overrun with nothing pointing at two independently correct
  measurements.

  `[ratchet] base_commit` is the commit a fragment's numbers were measured on, and
  `dropin.compose` refuses the fragment when `HEAD` does not contain it, naming the fragment,
  the gate and the sha. **Ancestry, not equality:** work landing on top of a measurement does
  not stale it, so a fragment that has been rebased forward still applies. **Absence is not a
  violation:** the field is hand-written, nothing in the tree writes a fragment to derive it
  at, and every fragment that predates it composes exactly as before — the alternative would
  have stopped every lane in flight. Nor is git's third answer a violation: where the head's
  history is not there to read, a tree copied without its `.git` or a shallow clone, the check
  has nothing to say (basicly-nwx4ku).

- **The differential's fold reads an edge in either spelling the log holds, and says which one
  it read.** `provenance.fold_edges` required `target`/`edge_type` while `migrate.py` writes
  `from`/`to`/`type`; `basicly-svct4w` fixed that side and asserted the mirror in a test so it
  could not be forgotten. `differential.views_from_events` had the defect the other way round:
  it read the engine pair only. Measured before the fix, against a positive control - four
  edges in the declared spelling read as **0**, the same four in the engine's as 4. The record
  predicted 1, and 1 was right about the fixture it came from, which holds three declared edges
  and one engine edge; 0 is what an all-declared fixture returns, because a reader matching
  neither key returns nothing rather than something.

  The pair table is read **out of `provenance`** rather than respelled here. A second copy of
  it is exactly how the two folds came to read different populations of one log, so a new
  by-path sibling loader was cheaper than the duplication. `edge_dialects` reports which
  spellings a log carries, for `EdgeFold.dialects`' reason: an empty edge set is otherwise the
  same answer for a log with no edges and a log whose every edge the reader could not parse,
  and those are opposite facts. A payload in neither spelling is still dropped rather than
  guessed into an edge.

  **The test that pinned the defect is now the control that both folds agree.** It asserted the
  1 deliberately and said in its docstring that the asymmetry belonged to the other module;
  that assertion now reads `== len(edge_fold.edges)` and compares the dialect reports directly,
  so a reader that *switched* spellings instead of accepting both still fails.

  This needed the module split first: `differential.py` sat at 11,110 tokens, exactly its
  frozen baseline, so not one line could be added. It is now three modules - the owned fold and
  the audit at 8309, the pure derivation at 3597, and the five records both sides report in at
  1236 - with the one-way direction of both seams checked by a cross-reference scan rather than
  assumed. Every name a consumer reads is re-exported by alias, so `except DifferentialError`
  and `kit.is_ready` are unchanged across fifteen call sites (basicly-oii83r).

- **A lane whose work is already committed now lands instead of being dispatched again.** A
  supervisor pass re-derives the landing-only set from git — commits ahead of base, a clean
  tree, no repair brief — so work that outlived a crashed supervisor reaches the merge queue
  rather than paying for a second implement run. (basicly-pjaudy)

- **A handoff artifact is now a typed `artifact` event, so the transport no longer cuts the
  body its consumer reads.** An artifact travelled as a `[harness-artifact]` comment marker,
  which put the JSON body in a `text` payload key — free text the per-event cap cuts at 4096
  bytes. JSON cut mid-token is not JSON, so the producer validated one payload and the
  consumer was handed a different one: measured over this repository's ledger on 2026-08-18,
  31 of the 54 artifacts ever written are stored cut, 337,353 bytes are gone, and all 23
  surviving truncated record-and-kind pairs are refused by their own entry predicate, against
  a control of intact ones that are admitted. `basicly.tracker.add_artifact` now appends one
  `artifact` event carrying the kind as a typed field and the body under `body`, a key
  `events.FOLD_READ_KEYS` names and the cap may never reach, and the read resolves that event
  first. A 22,621-byte plan that came back as a cut string through the marker now reads back
  byte-identical. **The retired marker stays readable**: its rows are on an append-only log
  and the cut bodies cannot be recovered, so a unit carrying only a marker still resolves to
  the artifact it holds and is refused naming the truncation and both byte counts rather than
  read as carrying nothing (`basicly-pp7q4i`).

- **A board snapshot's lock age is read by the supervisor's own reader.** The
  snapshot reported a holder heartbeat age derived independently of the code that
  decides liveness, so the board could disagree with the supervisor about whether a
  lock was stale (`basicly-rn0o.14`).

- **The curator is told the release-record field set, so a release record validates.** Its
  output contract said only "each unsupported claim named" while the schema sets
  `additionalProperties: false`, so the model invented a shape and two of three ship
  advances refused the record with `'suggested_wording' was unexpected`. The contract now
  names every field the schema requires and states that no other key is admitted
  (`basicly-s07cgc`).

- **`basicly board` now derives a loop phase for every record instead of eight, and the cap is
  removed rather than raised.** Measured on this repository before the fix, the wall's loop
  region read `intake 8 · classify 0 · decompose 0 · build 0 · verify 0 · validate 0 · ship 0`
  over 234 active units - not an idle factory but `board_facts.PHASE_LIMIT`, and a reader could
  not tell the two apart. `loop_state.read_node_state` is the only route to `derive_phase` and
  it reads the whole event log seven times per record, so a phase for the whole population was
  the 138 s the cap existed to avoid.

  **One fold, then the same derivation over it.** `loop_state.phase_map` reads the log once and
  folds it once, through a new `tracker.all_views` seam - one view already carries the status,
  the `external_ref` binding, the markers, the gate rows and the edges that `derive_phase`
  takes - so the population is one read and the phase is arithmetic over it. Measured on this
  repository's own log: **1036 records in 0.125 s**, against **128.1 s** for the 236 active
  ones through the per-record route, and the two paths agree on all 236. `basicly board` now
  builds in **534-555 ms** over four runs, with a phase on **236 of 236 units** where the
  region used to read `intake 8`. `PHASE_LIMIT` is gone, so no reader is left believing a
  bound still applies.

  **It calls the real derivation, and so do its inputs.** `policy.classify_gates` and
  `validate_gate.required_in` are the pure halves of `policy.gate_status` and
  `validate_gate.required_config`, split out so a caller holding folded rows classifies them
  the same way rather than spelling the rule a second time. The kit ships a `derive_phase` of
  its own and it is deliberately not the one used: it folds the ledger alone and cannot see the
  integrity level a unit's validate gate hangs off, so it reads `verify` where the engine reads
  `validate` - and renders identically. `tests/test_board_facts.py` pins the fold count at one
  with a spy rather than a duration, and pins that L3 case against its L2 control
  (`basicly-s1vqq2`).

- The base-checkout lock survives two Windows races: a release retried past a waiter's
  concurrent read (WinError 32), and a create refused by a peer's in-flight delete now
  reads as busy instead of crashing the dispatch. (basicly-s2obqz)

- **`basicly board serve` serves the same document `basicly board --out` writes.** The server
  folded with the supervisor lock facts alone, so a served board carried a phase on **0 of 232**
  units against the emitted board's 232, no `ready` flag at all, and a `repo` section holding
  only a name — every region on a live wall read `not emitted by this producer`. `board_facts`
  sits above the server's tier and cannot be imported there, so the caller now passes a builder
  rather than the server reaching for one, and a test binds the two so a third producer cannot
  diverge in silence (`basicly-sp8lce`).

- **The tracker kit's provenance fold now reads the edge dialect the engine actually writes,
  so `gating_edges` can see the population instead of answering for it with an empty set.**
  `provenance.fold_edges` required an edge payload spelled `target`/`edge_type` while
  `migrate.py` writes `from`/`to`/`type` and `differential.py` reads that spelling. Measured
  on this repo's own ledger: of 1,083 committed `edge` events, the fold read **0** and filed
  all 1,083 under `EdgeFold.malformed`, which nothing reads — so `gating_edges` returned an
  empty tuple, and empty is also the correct answer for a ledger with no edges at all. That
  is the fail-open shape, and it survived only because nothing in `src/` or `.scripts/` had
  wired the fold yet; a reader who wired it later would have inherited a silent zero.

  The engine's pair is now accepted on **read** and still never written, taken off
  `migrate.py`'s own constants rather than respelled, and chosen only when it is complete and
  the declared pair is not — so a payload in neither dialect is still refused, naming the
  documented spelling instead of guessing which writer produced it. `EdgeFold.dialects`
  reports how many events were read in each spelling, which is what makes an empty edge set
  distinguishable from an unreadable one. On the same ledger the fold now reads 1,065 edges
  with 0 malformed against `differential.views_from_events`'s 1,064 — they differ by the one
  retracted edge, which that fold models and this one deliberately does not. Unifying the
  spellings instead was rejected: `provenance.KEY_TARGET` is read by `fsck.EDGE_RECORD_KEYS`,
  so moving it is a writer change reaching two modules this fix does not own (basicly-svct4w).

- **The tracker kit's `fsck` now names a hole in a record's sequence chain, and it runs in the
  verify set instead of only when somebody thinks to run it by hand.** `fsck` over this
  repository's ledger reported one `broken` finding — `carried-totals` on a comment event of
  `basicly-vkh0.30` — and that was the consequence, not the defect. The record holds sequences
  1-8 and 10-34: **no event claims sequence 9**, so 33 events sit under a highest sequence of
  34. The fold is right that 33 events are there; the carried total of 10 on the event at
  sequence 10 is a faithful record of a writer that read a max of 9, so it was already wrong
  when it was written. §4.1 has the writer assign max+1 and the log has one append path, which
  makes the chain contiguous by construction — a hole means a line that was written is gone.

  `fsck` had `forked-sequence` for two events claiming one number and nothing for a number no
  event claims, so it reported the disagreement instead of the cause. §4.6 already voids a
  *forked* item's carried totals so one root defect does not print as a page of findings; a gap
  voids them for the same reason and was not handled. Measured on a seeded ledger: the old
  checker printed **two** `carried-totals` findings for **one** missing event and never
  mentioned the gap. It now prints one `sequence-gap` naming the missing number, and carries no
  event ids — the events either side are sound, and pointing a reader at them is the wrong
  report.

  The event itself was not repaired and cannot be: an append-only log has no undelete, and no
  commit in this repository's history ever contained a sequence 9 line for that record. So the
  new `ledger-fsck` check declares it in `[tool.ledger_fsck.frozen]`, keyed `<record>/<kind>`
  so an allowance for one defect cannot absorb a different one landing on the same record, and
  binds on everything else — a new finding, a recorded one that grew, or a recorded one that
  fell and was not banked. It costs 0.18s over 6,157 events and 1,005 records, so it runs in
  both modes (basicly-t10ipy).

- **A worktree's tracker redirect is resolved in exactly one place, so the read and the write
  cannot reach different checkouts.** A lane's worktree carries a one-line `redirect` naming the
  checkout that owns the tracker, and the rule for reading it was implemented four times inside
  the package under three different rules, with the landing's own id check not resolving it at
  all. Two of the four already disagreed: one honoured a redirect only when the target directory
  carried the expected name, the others honoured any directory, so a redirect naming anything
  else sent the tracker read to one checkout and the event-log write to another — the failure
  that had already silently discarded every tracker call made from inside a lane.

  Measured on a temporary repository whose redirect names a differently-named directory: two
  readers answered `base/tracker` and `wt` before and both answer `base` after, and the
  landing's id set answered the worktree's own ids in every redirected case, control included.
  Two further defects the tests found on the way — an empty redirect file resolved to the
  **process working directory**, because `Path("")` is `Path(".")` and reads as a directory, and
  the one resolver refuses it; and a JSON array line in the tracker export crashed the id read
  with `AttributeError` where another of the four copies had skipped it. The standalone hook
  script keeps its own copy, which cannot import the package, and a parity test holds it to the
  same rule (basicly-tcmy.19).

- **A push that would race a landing is refused naming the contention, instead of dying on a
  stash.** `pre-commit` stashes the unstaged tree before it runs the pre-push stage and
  restores it after. A landing writing the ledger inside that window changes the tree under
  the stash, so the restore conflicts and the push aborts with `Stashed changes conflicted
  with hook auto-fixes`. The commits are intact, and the message names a git mechanism rather
  than the fault, so an operator reads a local mistake instead of two engine operations
  racing - the third surface of one class, after `basicly-kjc5.63` on the base checkout
  commit. On every surface the text named git and never contention, so it was diagnosed
  wrongly every time.

  The refusal runs **before the stash exists**, so there is nothing to conflict, and it says
  the three things the stash message withheld: which pid holds the tree, that the git text the
  operator is about to see is about the stash and not the fault, and that their commits are
  unaffected. The signal is deterministic rather than a guess: the ledger's lock is a file
  whose existence is the lock, carrying its holder's pid, and both the lock's name and the
  liveness rule are read from the kit's own `events.py` rather than respelled - a second
  spelling of either is the drift that module documents as the defect this design keeps paying
  for.

  **It fails quiet in every ambiguous case, and that is deliberate.** No kit installed, no
  lock file, a lock it cannot parse, a pid that is gone, and a pid the platform cannot judge -
  Windows has no stdlib liveness probe, because `os.kill(pid, 0)` there calls
  `TerminateProcess` and would kill the process it was asking about. Refusing on the hook's
  own uncertainty would make it look like contention, and would block every push on that
  platform for as long as a stale lock file sat on disk. Each of the three liveness answers is
  injected as test data rather than raced, so the verdict is a property of the fixture and not
  of whichever machine ran it.

  Two defects in this change were caught only by running the real hook end to end rather than
  by its unit tests: a parenthesised `except` clause the house form forbids where nothing
  binds, and a dynamically loaded module typed as `object`, which broke pyright on every
  attribute it reached (basicly-u3b65o).

- **A gate run in a worktree no longer prints a `VIRTUAL_ENV` warning `uv` was already ignoring.**
  The engine is launched with `uv run` from the base checkout, so every verify check and lane
  dispatch it started in a worktree inherited that checkout's `VIRTUAL_ENV` and warned once per
  `uv run` check. It is now dropped when the cwd is a different checkout, and kept when it is not. (basicly-uq3pki)

- **A long gate name no longer paints over the check below it, and the measurement that missed
  it now exists.** The footer's gate strip reserves a grid of one-line rows, but only the
  *cell* was held to one line - the name inside it was free to wrap, and did:
  `projection-permissions` and `declared-dependencies` each took a second line the row height
  had already allotted to `noqa-debt` and `ledger-bodies` and were drawn across them. Measured
  on this repository's own snapshot at six widths, the collision is present from 1200px to
  1800px and absent at 1920px, which is why the layout passed review. Six columns hold the
  tree's longest check name only at 1920px and no column count holds it at every width the
  layout claims, so the name now declares its truncation the way every other text on the page
  does rather than wrapping.

  **The instrument is the more durable half.** `.scripts/check_render_overflow.py` reported
  zero on the broken wall and was *right* to: an element painted across its neighbour does not
  overflow - its content fits its own box, the box is simply in the same place as another box.
  Overlap and overflow are different faults, so the script now measures both in one browser
  pass and reports them as two independent signals under two prefixes, `render-overflow` and
  `render-overlap`, with the exit code their disjunction and a refusal printed under both. The
  overlap signal is a pairwise bounding-box intersection over the *outermost* elements that
  carry their own text; that qualifier is a false-positive class, not a detail - an inline
  box is its font's em box rather than its line box, so a monospace glyph inside a sans line
  produced six spurious pairs on the live board against one real collision. Elements out of
  normal flow, invisible elements, and an ancestor holding its own descendant are excluded for
  the same reason.

  Two committed fixtures prove the two signals discriminate, each being the positive control
  for one and the negative control for the other: `tests/fixtures/render/clipped-and-not.html`
  reports 1 clip and 0 collisions, and the new
  `tests/fixtures/render/overlapping-and-not.html` reports 0 clips and 1 collision, alongside
  three quiet controls - boxes that merely touch, a parent carrying text around a child
  carrying text, and an absolutely positioned overlay. Both signals now report nothing on the
  live board at 1920x1080 and at 1200x900, where before the fix the overlap signal named both
  reported pairs and the clip signal named neither. A test binds the render fixture's longest
  gate name to this repository's own `[[verify.checks]]`, so a longer check name landing fails
  in the suite rather than on the wall (`basicly-uvpu6b`).

- **`docs-citations` no longer passes over a citation it cannot read.** Two defects in one
  gate, and the second is the one that matters. A citation whose path is backticked and whose
  line number is not — `` `loop.py`:120 `` — matched nothing at all, so it was not counted,
  not checked and not reported: the one outcome a presence-based gate cannot tell apart from
  a document that carries no citations. The cause is not the lookbehind the report named,
  which passes against an opening backtick, but the **closing** one, which sits between the
  path and the colon whenever an author ticks the path and leaves the number outside. The
  pattern now takes that tick as part of the boundary. Loosening it admits no prose: over all
  304 tracked `.md` and `.yaml` files the old pattern and the new one both find 53 citations,
  and a document writing a clock time, a ratio, a count after a backticked command and a bare
  continuation reference yields none of them.

  The second: the gate was **fail-open on every citation it could not verify**. Its symbol
  rule only runs when the citing sentence names a top-level symbol of the module it cites,
  and 32 of the 44 citations in `docs/` name none — so they were counted, reported as a
  coverage share, and passed. Probed on real input, a sentence citing a real module at line 1
  with a false claim about what is there was one of the 32, and the gate exited zero over it.
  Each such citation is now a finding of its own kind, ratcheted per document in
  `[tool.docs_citations.unverifiable]`: the 32 that existed are recorded debt that may only
  fall, and a document absent from the list may carry none. The repair a reader is sent to
  make is to name, in the citing sentence, the symbol the claim is actually about — which is
  what makes the claim checkable, and what the symbol rule then holds it to (basicly-v5c8ob).

- The board's alarm band now colours by how long an ask has waited, not by whether one exists: a wait past one hour turns orange, a shorter one stays amber. The detail line states the wait once, as an absolute since-when plus the offered action, instead of repeating the headline's duration in a second unit. (basicly-v8jwf0)

- **The per-event size cap now bounds by event kind, not by the spelling of a payload key.**
  The cap dispatched on a closed four-member list of key *names*, which was wrong in both
  directions. `value` was on the list and the fold reads `value`, so a tracker field the fold
  derives state from was being cut at 4096 bytes — one record's description is permanently
  truncated in the store of record. And any key the list did not name was not capped at all, so
  the bound a new event kind received was decided by the word its author happened to pick rather
  than by a decision. Two spellings of the same description therefore carried two different
  bounds: whole under `description` on a `created` event, cut under `value` on a `field` event.
  Now `FOLD_READ_KEYS` names every key the fold and its delegates read and the cap may never cut
  one; `KIND_TEXT_BYTES` declares the bound per kind; every other string is cut by default, so a
  new key is bounded without anyone remembering to name it; and **a kind that declares no bound
  is refused rather than stored unbounded**. This is the precondition for carrying a handoff
  artifact as a typed event (basicly-vbl35a).

- **A `create` carrying a bare word where a flag belongs is refused, naming the word and the
  flags that would have carried it, instead of dropping it.** `basicly tracker write -- create
  "<title>" bug 1 --description "..."` minted a record with `issue_type` and `priority` both
  unset and printed `created:` — `--type` and `--priority` are flags, so `bug` and `1` landed as
  extra positionals and `write_verbs._create_drafts` ignored them. It caught the operator twice
  in one session, and the second time the untyped record was what the sizing path then
  misreported.

  **The silence is the defect, not the parsing.** A caller who wrote `bug 1` believed they had
  typed the record; nothing said otherwise, and the next reader sees an untyped record with no
  trace that a type was offered. `_create_drafts` twelve lines above already refused a create
  naming no title, on the argument that a titleless record is a `created` event stating nothing
  (basicly-1qi0sz) — an argument that covers an argument the seam cannot place just as well, and
  was not being reused.

  **Arity is not the discriminator, which is why the refusal is scoped to one verb.** br closes
  `[IDS]...` and `update` takes the same, so a refusal keyed on "more than one positional" would
  refuse every plural close in the log; the two `dep` verbs and `gate report` each check an exact
  arity of their own already. `create` was the one verb with a fixed shape — `create <title>` —
  and no check on it. For `close` and `update` the further words are record ids, which
  `owned_write.refuse_a_write_to_an_absent_record` already speaks for, so nothing is silently
  dropped there.

  The flags the message names are read off `tracker_argv.CREATE_FIELD_FLAGS` rather than
  respelled, long spellings only, so a flag added to that table joins the refusal's advice by
  existing — and a caller who wrote a bare word wrote no flag at all, so the short forms would
  only be noise. Both size ratchets were measured before the prose was written: the refusal is
  in `write_verbs.py`, which had 170 tokens of working-set headroom and 636 of prose, and the
  derived constant is in `tracker_argv.py`, which had 2668 of headroom and 41 of prose — the two
  axes pull opposite ways and the first draft tripped both (basicly-ve0b7d).

- **The suite's live-ledger guard no longer undoes the write it catches.** It read the
  committed event log's bytes around every test and, on any difference, wrote the old
  bytes back and failed the test in flight — a hand edit to an append-only log, racing a
  writer holding the ledger lock the test process never takes. Measured 2026-08-19 in the
  base checkout: twenty `basicly tracker write` calls issued while `pytest` ran produced
  zero events, and the same twenty in a quiet tree landed twenty of twenty. The guard now
  attributes a change through a PEP 578 `open` audit hook, which sees every route
  including a kit loaded by path: a write from the test process fails that test and names
  it, a change from another process is reported once per path as unattributed, and in
  neither case is a byte restored (`basicly-vkh0.51`).

- **The session spend the board shows now advances while a lane runs.** It read `0%` for a
  whole pass because the recorded figure only moves when a run record lands; `spent_tokens_live`
  adds each running lane's live-reported tokens, marked as an over-estimate against the recorded
  spend, which the D3 grant gate still binds on unchanged. (basicly-wctp0g)

- **The rebaseline count reports loosenings, not files, so accumulation on one file is
  visible.** `dropin.compose` keyed `rebaselined` by entry and kept the last fragment that
  declared it, so N fragments each loosening one file were reported as one. On this tree that
  was **41 declarations reported as 19**: `tests/test_loop.py` carried four - 311, 146, 13 and
  109 - reported as one, and `merge.py` three. The gate's summary line now reads
  `41 rebaselined across 19 entries`, and names the two apart only when they differ.

  Every entry always bound individually - a deliberately short delta fails the gate, which is
  the positive control - so nothing was ever wrongly admitted. What was wrong is the number an
  operator reads to judge how much debt a file has taken: **it was the count of files, and the
  whole point of counting is to watch debt accumulate on a file.** Per-entry counting was
  exactly blind to accumulation, which is the property `basicly.d/README.md` claimed it
  guaranteed. That claim now states what the count really prevents - each loosening is
  visible, one file taking several is not stopped - carries the 41-against-19 measurement, and
  says that a file appearing in several fragments is the signal to split it.

  The composed baseline is unchanged and asserted as the control: accumulating the *names* must
  not move the *arithmetic*. `Baseline.rebaselined` is now entry to every declaring fragment
  rather than to the last one, which is the only interface change.

  **This finding explains three earlier ones in the same pass.** `tests/test_handoff.py` had
  been rebaselined three times and `merge.py` three, and both were discovered by reading
  `basicly.d/` by hand while the gate reported one apiece - so the instrument that would have
  shown the accumulation was the one being fixed (basicly-wpqdag).

- **A handoff artifact the event cap cut is reported as truncated, with both byte counts,
  instead of as a malformed artifact.** The entry predicate refused a stored artifact that had
  been cut at 4,096 bytes with a schema type violation on the top-level instance and a
  300-character fragment of the cut JSON, so a transport that destroyed a valid artifact read as
  a producer that wrote a broken one — and a blocked record's own status surface named a
  different, coexisting cause, so nothing anywhere named the real one. The reason now names the
  cut, the stored length, the original length, and re-recording from the producing state as the
  remedy, because the body cannot be recovered from an append-only log.

  Measured across the whole ledger when it landed [2026-08-18]: 23 record-and-kind pairs held
  cut artifacts, 23 of 23 name truncation, 24 of 24 uncut pairs are still admitted, and nothing
  is falsely called truncated — an artifact that is malformed but whole reports its schema
  violations unchanged. The flag had never reached the reader: the row projection reduced every
  payload to text and a stamp, discarding the two truncation keys one seam below the predicate.
  They are now carried both-or-neither, which is a real constraint rather than tidiness, because
  the naive carry emitted a flag with a null length.

  **This does not stop the truncation**, only the misreporting of it (basicly-wug2o2).

- **A unit parked in `validate` is advanced by a supervised pass instead of only counted by
  the WIP bound.** `wip.DOWNSTREAM_PHASES` has counted `verify`, `validate` and `ship` since
  the VALIDATE phase landed, while `supervise.advance_parked` drove `verify` and `ship` only.
  A unit whose derived phase was `validate` was therefore charged against
  `[policy] max_downstream_wip` and advanced by nothing, so five of them refused every further
  dispatch and the queued decision told the operator to land lanes the pass could not land.

  `advance_parked` now drives `wip.DOWNSTREAM_PHASES` itself — one definition, imported rather
  than respelled — and both supervised drives (`advance_parked` and the post-ship drive in
  `_land_green`) name the session as their grant root, so the validator those drives can now
  spawn is refused by D3's spend halt before it starts rather than running unmetered inside a
  landing pass (`basicly-xab3`).

- **A validator whose reply carried no verdict no longer leaves the loop in exactly the
  state it was in.** The `validate` advance dispatches a validator against the merged
  change and reads its `VALIDATION: PASS`/`FAIL` line off the reply; when there is no such
  line there is no verdict to record, and the advance used to return having written nothing
  anywhere — no gate event, no queue item, no rework — so `loop status` reported the same
  gate and `loop decisions` the same empty queue as before the dispatch, and the only
  surface that showed the run at all was the spend. Measured on `basicly-gvlpxm`: one
  advance dispatched a validator and two reviewers, all exiting 0, one of them alone
  charging $1.13, and the ledger's gate-event count for the record did not move. The reply
  is not stored and the run record carries usage rather than text, so nothing after the
  fact could recover what the validator had said. It is now queued as a `validate` decision
  carrying that reply, and the advance blocks on it — an unreadable verdict is a fact an
  operator can dispose of, where silence is not. A verdict that *is* readable still records
  the gate exactly as before, and an advance that dispatched no validator still records
  nothing: a fix that wrote a gate event unconditionally would have turned a fail-silent
  into a fail-open. The refusal a recorded `FAIL` prints also stops claiming that no result
  was recorded, which was the same confusion in a second place (basicly-xd79u3).

- `basicly loop supervise <leaf>` now seeds a childless root as its own single lane
  instead of reading it as an exhausted epic and exiting 1 — the surface `preflight`
  already priced as one lane. (basicly-xkaya9)

- **The board producer is four modules where it was two, so a further board unit fits.**
  `board_fields` had 18 tokens of size headroom and `board_snapshot` 23, and units C, D and E
  of the board all consume them, so none of them could be built. The split line was already
  nameable rather than arbitrary and it is the one the record named: what may cross the wire
  against which rows a section is, and then which sources a section reads against the document
  that assembles them.

  | module | tokens | headroom | holds |
  | --- | --- | --- | --- |
  | `board_fields` | 1607 | 18 → 2393 | the bounds, and a marker as fields |
  | `board_sections` | 2846 | new → 1154 | the six row reducers, and `LaneFacts` |
  | `board_snapshot` | 2845 | 23 → 1155 | the ledger half, and `build_document` |
  | `board_usage` | 1324 | new → 2676 | the `.basicly/usage/` sections |

  Neither seam imports back: a reducer needs the bounds and the bounds need no reducer; the
  assembler needs the usage sections and they need no assembler. Both new modules got their own
  tier in `.importlinter` under the `exhaustive` contract, so a maintainer decided where each
  sits rather than the gate inferring it, and `lint-imports` reports 2 kept and 0 broken. The
  architecture's layering block is **regenerated** rather than edited, as the record required:
  39 tiers to 41, 105 modules to 107. The test modules were split to match, which
  `check_test_naming` then required rather than suggested - it refused `board_usage` for having
  no test file named after it, which is the drift that gate exists to stop.

  **One of this record's acceptance criteria is refuted, with the measurement.** It asked for no
  new density waiver. Splitting a module raises the prose share of *both* halves by
  construction: the code divides and the contract docstring does not. `board_snapshot` lost 630
  tokens of code and 505 of prose in one edit, so it got smaller and denser at once - 3980 to
  2845 tokens, 47% to 51.5%. Every cheaper reduction was taken first and measured at each step,
  reaching 51.5% from 55.7% and 55.1%: prose moved to the module whose code it describes, two
  stale cross-references the split itself created were repointed rather than kept, and two
  restatements of what a test asserts were dropped. What remains is measurements - the 93-fold
  6.1 s cost of `observe()`, the `supervise -> board_snapshot -> supervise` cycle, the 140/203/1
  ask pairing - and a ruff `D`-mandated `Args:` block that is a third of what is left. Two
  stated waivers were taken instead of deleting those, and no rebalancing avoids them: folding
  `board_usage` back gives 4169 tokens against the 4000 cap, and extracting the three
  caller-supplied facts records instead gives a module that is 89% prose (basicly-y754k2).

- **A dispatched lane is told a working directory inside its own worktree, instead of being
  left to pick a session-wide scratchpad two lanes share.** The dispatch brief already said
  the lane sits in a dedicated worktree, and said nothing about where to put a script or a
  measurement, so a lane used the session scratchpad — which is keyed by session and not by
  lane. Measured on 2026-08-20 during a nine-lane pass: a sibling overwrote a lane's
  measurement script between the write and the run, and the run **printed the sibling's files
  and numbers under the first lane's command**, with no error. Six lanes' copies of files sat
  in one shared backup directory, so a restore would have written another lane's content into
  the wrong worktree.

  The failure is silent substitution rather than loss, which is why no positive control on the
  measured corpus catches it: the corpus was never wrong, the script was. The brief now names
  `.basicly/usage/scratch` and the mechanism together, because a rule without its failure mode
  reads as tidiness. The path is relative, which is what carries the isolation — it resolves
  against the lane's own worktree, so two lanes handed the identical brief still write two
  directories, and it sits under the already self-ignored `.basicly/usage/` so nothing a lane
  scribbles can reach a commit. `needs_input.SENTINEL_FILE` was checked as a second site of the
  same shape and is already relative. The close-out guidance names cross-lane substitution
  beside the cleaning hazard, which landed separately (basicly-z9xvwa).

- Board lane cards no longer report a provisioned-but-idle worktree as `live`; the board document's `live` now means an agent is actually running the lane, and the worktree fact travels separately as `provisioned`. (basicly-ze0po3)

### Security

- **A credential-shaped value is masked before it can reach the committed event log.** On
  2026-08-16 a comment body was passed to a shell inside double quotes with backticks in it; the
  shell ran them as command substitution and one expanded the whole environment into the write —
  152 assignments, 40,325 characters, including a live 32-character session token. It went
  through the write seam, so it reached the store, and running the existing redaction afterwards
  changed nothing relevant. The ledger is append-only and ships in every clone, so this is the
  one surface with no undo; nothing had been committed, which is the only reason the repair was
  an edit to two working files rather than a history rewrite.

  The three existing rule sets were blind to it by construction. The value was 32 characters of
  hex with no prefix, so nothing about its *shape* identified it, it was not a path, and the
  variable names were not the running user's. What identifies it is the name beside the equals
  sign: an uppercase identifier ending in `TOKEN`, `SECRET`, `KEY`, `PASSWORD` or `PASSWD`
  followed by a value, and separately a run of `NAME=value` lines, which is a dump whether or
  not any single line looks like a credential. Both are now redacted to a labelled placeholder
  at the write, and `redact_committed` is composed as environment, then secrets, then paths, then
  identity.

  **The larger hole was that second stage.** `redact_secrets` — the high-signal credential
  shapes — had only ever run on surfaced runner output and never on the committed path, so a
  hand-written credential in a recognised format went into permanent history verbatim. It runs
  there now, and across this repository's docs, source, tests and catalog it matches zero lines,
  so closing it cost nothing. Driven end to end through the real write seam into a throwaway
  ledger with the previous redaction as the control, and the stored ledger was probed
  separately, because a write-time guard says nothing about what is already on disk
  (basicly-vkh0.33).

## v0.9.0 - 2026-08-14

Delta: v0.8.0..v0.9.0

### Added

- **`basicly usage tuning` advises every governed factory parameter from the outcomes it
  actually produced.** Almost every number governing the factory was set by judgment and then
  never revisited; this is the readable half of the feedback loop the exceptions already had.
  It reads the dispatch ledger from **both** corpora — the self-ignored local run records and
  the committed `[harness-run]` markers, deduplicated so a dispatch in both is one sample —
  and names which corpus each sample came from. Per parameter it reports the value in force
  for the dispatches it summarises (a session override puts its dispatches in their own
  cohort with their own outcome distribution) and a recommendation with its sample size:
  `measured` at or above `[policy.sizing] calibration_min_samples`, otherwise `seeded`, where
  the declared prior stands and the row names the in-force value it would displace — never a
  number fitted to three samples wearing a "seeded" label. A parameter the ledger records
  nothing about still prints, with a sample size of zero, no recommendation and the reason it
  has none: `stall_after`, `quiet_after`, `max_agent_processes`, `[worktree] concurrency` and
  the two calibration bounds are all in that state, and a bound nothing records is a bound
  nobody can tighten. **It is advisory and writes nothing** — applying a recommendation stays
  a human's or a gate's call (basicly-3ifz.1).

- **Seven specialist roles drive the loop, and the engine now dispatches them by phase.** The
  factory's states have named their specialists in prose since the requirements were written, and
  nothing consumed the names: every dispatch ended at a bare `claude -p`, so one default runner
  served every phase. `basicly.roles` closes that with a table — `classify` → `decider`,
  `decompose` → `decomposer`, `build` and `repair` → `implementer`, `validate` → `validator`,
  `ship` → `curator`, `retrospective` → `retrospector` — and the runner puts `--agent <role>` on
  the argv.

  The map is **data, not judgment**: a phase resolves to exactly one role by lookup, so the choice
  costs no tokens, cannot drift between lanes and is not gameable. Resolution falls to the default
  runner rather than failing in three cases, each deliberate — a phase with no persona (verify is
  deterministic gates by decision), a family that cannot select one (codex ships no subagent root),
  and a role whose *projected* file is absent, checked against the file the host reads rather than
  the catalog source. A consumer on an older install therefore gets an unspecialised loop instead
  of a stopped one.

  Eleven agents are authored as catalog sources under `.basicly/core/agents/`, projected into both
  agent roots by `basicly agents-build` and **vendored to consumers by `basicly install`**: the
  seven loop roles above plus `code-reviewer`, `security-auditor`, `test-runner` and `researcher`.
  The projected `tools:` allowlist was verified to bind on copilot as well as claude, in the
  spellings we already emit, against a positive control (`basicly-4kdm`, `basicly-4xmu`).

- **Five loop skills, each paired to the role that loads it.** `decompose-plan`,
  `validate-as-consumer`, `repair-in-place`, `root-cause` and `python-guidelines` ship as catalog
  sources and are named in their agent's declared skills. `catalog lint` refuses a name that
  resolves to nothing, so the pairing is a checked relation rather than a sentence in a document
  (`basicly-4kdm`, `basicly-u2hl.52`).

- **A dispatched lane's transcript now names the tools each turn called**, so a lane's token
  spend splits into context acquisition and implementation. That split is what `basicly-ejdm`
  reasons about and had no instrument for: the claim that a lane's multi-million-token floor is
  "bought by the instruction" was unfalsifiable without it.

  Claude only — codex emits no per-tool event this stack parses, and the report that consumes
  this must say so rather than implying coverage. A turn that called nothing records an empty
  list; a transcript line written before the field stays absent, so a reader can tell "called no
  tools" from "predates the measurement".

- **`basicly tracker shadow` runs the work-tracker cutover's shadow differential against the
  live tracker.** Step 2 of `docs/design/work-tracker.md` §5 had every piece of machinery and
  no driver: the comparison could only be constructed by a test. The command folds the owned
  event log under `.basicly/ledger/` and holds its answers to phase derivation, the ready set
  and gate status against `br` itself — `br list -a`, one `br show` per hundred ids, and
  `br gate list` for the query no export can answer, since a `gate report` row is absent from
  the JSONL export. The reference is live and never a re-import, which the kit enforces by
  perturbing the ledger and refusing a source whose answers move with it; the reference
  therefore re-reads the tracker rather than caching, because a memoised answer would clear
  that probe without being independent. The run writes to neither store and reports `clean`
  and `conclusive` as two verdicts, so agreement on a query every record answered identically
  cannot be read as evidence. First run against this repo's 643-record ledger: 331 gate
  disagreements (no export carries a gate row, so the import could not have carried one), one
  phase disagreement on a bead whose worktree binding never reached the committed export, and
  three records the tracker holds that the ledger does not (basicly-f6th).

- **A consumer now has a written path from `basicly install` to a first shipped bead.**
  `docs/` carried a reference (the architecture file) and explanation (design and research),
  and nothing else — a repo could install the harness, read the whole architecture, and still
  not know which command comes after `install`. The new layer closes the two missing Diátaxis
  quadrants: a tutorial (`docs/tutorial/first-loop.md`) that walks a scratch repo from install
  through filing a bead, the classify checkpoint, building in the provisioned worktree, the
  landing and the ship approval; and six task-focused how-tos (`docs/how-to/`) for the
  recurring operations — customizing the catalog through the overlay, wiring the verify gate
  (which passes *vacuously* until you declare checks), unblocking a commit a hook refused,
  upgrading and detecting drift, running parallel lanes, and resuming or handing over a track.
  Every command and quoted output in the tutorial was executed against a fresh repo before it
  was written, which is how it came to document the two gates that refuse a fresh install's own
  first commit: `catalog-lint` demanding a `[catalog] rank1_floor`, and the missing beads issue
  id. README and architecture §13.1 point at the layer; §15's roadmap row moves to `shipped`
  (basicly-imnu.2).

- **A `PreToolUse` guard refuses a for-loop over an unsplit scalar.** zsh does not word-split an
  unquoted scalar, so `V="a b c"; for x in $V` runs the body once with the whole string and exits
  0 — writing nothing while reading as success. The guard blocks that shape at tool time and names
  the variable. It matches only an assignment and its loop in the same command, which is complete
  because shell state does not persist between tool calls, and leaves arrays, inline lists, quoted
  expansions and command substitution alone.

- **Catalog routing is gated deterministically, at zero token cost.** `basicly catalog lint`
  now ranks every model-invoked entry's description with a stemmed TF-IDF ranker (pure Python,
  no new dependency, no embeddings) and enforces three assertions: a positive prompt ranks its
  owning entry in the top-k, a negative prompt is outranked by the different entry it declares
  as `owner`, and no two descriptions exceed a pairwise similarity ceiling (error at 75%,
  warning at 50%). The evidence is a per-entry `evals.yaml` colocated with the catalog source
  and never projected into a skill root. The CI metric is the **rank-1 rate**, printed on every
  run and checked against `[catalog] rank1_floor` in `basicly.toml`; a companion
  `rank1_floor_high_water` ratchets that floor so it can be raised but never lowered, because
  lowering a floor to make a regression pass is deleting the test while looking like
  maintenance. A prompt that scores zero fails instead of passing on a tie-break, so an
  assertion cannot report coverage it never had. Authoring the corpus found five descriptions
  missing vocabulary users actually say — `tool-fd`, `tool-ripgrep`, `tool-sd`, `tool-typos`
  and `tool-uv` — and one stemmer defect that stopped "what branch am I on" reaching `tool-git`
  (basicly-m4zv.2).

- **A judged finding must carry a severity, and a reviewer bundle may not pre-judge the review.**
  Two deterministic checks, both free at CI time. A judged rubric check answering `no` is now a
  finding that must classify itself `BLOCKER` / `IMPORTANT` / `MINOR`; one that does not is
  rejected as a schema violation and re-requested once with the violation named, exactly as an
  unparseable reply would be, rather than accepted as a dispute nobody can triage. The invariant
  sits on the verdict record itself, so there is no path — parse, construct, or report — to a
  severity-less judged finding, and the severity rides onto both the `rubric-judged` gate note and
  the queued validate decision. Separately, every reviewer bundle the engine assembles (the
  semantic-review prompt and the rubric judge prompt) is linted for finding-suppressing directives
  — "do not flag", "don't treat X as a defect", "at most Minor", "the plan chose" — and refused
  rather than emitted weakened; the lint covers the material under review as well as the task
  text, because a reviewer reads one prompt and cannot tell instruction from evidence
  (basicly-m4zv.4).

- **Every gate is classified by type, so "what happens when this one fails" is answered by the type
  rather than per call site.** `policy.GATE_TYPE_BY_GATE` types each gate the engine names as
  pre-flight, revision, escalation or abort, and defaults an unnamed one to revision. A pre-flight
  gate is additionally refused the tracker while it runs, so it cannot write state before the work
  it guards exists.

  The two rules that govern adding one are recorded with it: selection starts at pre-flight and
  moves only when a check must run after work is produced, and a cap is sized to the cost of one
  iteration — a landing bounce and a re-review of a three-line fix must not share a budget
  (`basicly-m4zv.6`).

- **Five of the seven unbuilt handoff artifact kinds now have schemas**: `classification`,
  `change-shape`, `verification-evidence`, `validation-transcript` and `release-record`. Their
  absence is why `validator`, `curator`, `retrospector` and `reviewer` were authored and
  unreachable — a role with no schema has nothing a state can validate, so no state dispatches
  it.

  Each is strict in the same way the two existing schemas are: `additionalProperties: false` at
  every object level, a `required` array naming every declared property, and `schema_version`
  pinned. `classification` is asserted against a payload built from `integrity.assign()` rather
  than a hand-written example, because a schema agreeing with an example someone wrote for it
  proves nothing.

  The requirements' artifact table said "Six schemas" while listing seven rows and omitting
  `release-record` entirely — corrected, with the row added. `solution-design` is the one
  remaining kind with no schema, and deliberately: D17 specifies it as markdown sections rather
  than a JSON payload, so that is an open question (`basicly-32qz`) rather than an omission.

- **Every supervised dispatch now leaves a transcript.** A lane's `stream-json` output was read
  into memory, spent entirely on token accounting and dropped when the process exited: measured
  on 2026-08-08, 32 dispatches costing $122.41 left records of what each one cost and nothing of
  what it did, so no claim about lane behaviour could be evidenced after the fact. Each dispatch
  now writes `.basicly/usage/lane-logs/<session>/<bead>.jsonl` as the events arrive, flushed per
  event so a lane stopped by a quiet bound, a spend ceiling or a hard kill keeps what it had
  already said. The supervisor's own narrative — the session header, every dispatch line and
  every routed outcome — is teed to `pass.log` in the same directory, where before it existed
  only in a terminal pane. Both are redacted, both sit under the self-ignored `.basicly/usage/`
  tree, and `[runner] lane_log_sessions` bounds how many sessions are kept before the least
  recently written rotate away.

- **A plan gate on entry to BUILD refuses a unit of work the loop cannot hold to.** Every child
  in a plan must now declare all five of: acceptance criteria, at least one scope glob, a
  dependency list, a token budget (`budget_tokens`) and an integrity level (`integrity`, one of
  `L1`/`L2`/`L3`). A plan missing any of them is refused when it is loaded — by
  `basicly decompose --plan`, by `--children`, and by the loop's own child-plan proposer, which
  blocks for a human rather than recording it — and the refusal names every missing field on
  every child in one message instead of one per round trip. The inspection sits before BUILD,
  which is where nearly all the tokens go, so a plan defect is found while it is still cheap.
- **Decompose emits a dependency graph instead of deriving one from scope overlap alone.** A
  child's `depends_on` names sibling titles (the plan is written before any issue exists), and
  each declared edge is recorded on the tracker as a `blocks` dependency, so `br dep tree`
  carries ordering that no glob comparison can express — B needing A's decision when the two
  touch no common file. Declared edges are unioned with the scope-derived serial chain and
  deduplicated. A cycle in the declared graph is refused **naming its members**, and no issue is
  created: a half-recorded decomposition is worse than none. An empty `depends_on` is a
  declaration; an absent one is not, and is refused.
- **Each created child records its plan fields in a `## Plan` section**, and
  `plan_gate.build_entry_verdict` reads them back to decide whether a lane may be dispatched,
  naming the field a unit is missing. It fails closed on an unreadable record (`basicly-u2hl.1`).

- **`noqa-debt` is a `[[verify.checks]]` entry: lint suppressions are ratcheted per code and cannot
  grow silently.** `.scripts/check_noqa_debt.py` counts `# noqa` by rule code and fails on an
  increase against the counts frozen in `[tool.noqa_debt]`. Counting is by `tokenize` comment and
  ruff's own directive grammar rather than by substring, so a comment that *looks* like a
  suppression and suppresses nothing is not credited as one.

  It also ratchets `unreasoned_count` in both directions, against a house form of
  `# noqa: CODE — reason`. The argument the gate makes is its own history: the figure was stale
  twice while it was prose, and every suppression it now counts arrived through a green gate
  (`basicly-u2hl.12`).

- **The `python-guidelines` skill carries the design calls no linter makes, and it activates on
  Python files rather than waiting to be asked.** Where an oversized module splits, whether a name
  or a docstring carries meaning, whether an abstraction earns its keep, `noqa` legitimacy,
  exception design, 3.14 idiom selection, free-threading safety, and the rule that a comment
  contradicting the code is a defect in which the code is what ships — none of which any rule in
  the stack can read.

  It stays a skill and takes a `paths: ["**/*.py"]` glob, which limits *and triggers* automatic
  activation, so it binds on every Python edit for **zero** always-on characters. The glob sits
  under the skill schema's `claude:` vendor fence because `paths` is outside the portable Agent
  Skills subset, which keeps every projected `SKILL.md` portable while still expressing the
  host-specific capability. Codex has no glob-based instruction scoping and still relies on model
  invocation there — a parity gap declared rather than papered over (`basicly-u2hl.13`,
  `basicly-u2hl.17`).

- **A source module with no test file named after it now fails a gate, by name.** `§9.4` of
  `docs/design/factory-loop-requirements.md` states the convention — `test_<module>.py`, or
  `test_<module>_<aspect>.py` when one module's tests justify a split — and records that it was
  *emergent* when it was measured: 48 modules, 84 test files, every module covered. Nothing made
  it binding, so the first module splits broke it. Measured on this tree before the fix: 73 source
  units and **11 with no test file named after them**, ten of them created on 2026-08-08. Their
  tests were never missing; they stayed in the file named after the module they were extracted
  from, which is exactly the drift the convention existed to stop. `.scripts/check_test_naming.py`
  is now a `[[verify.checks]]` entry (`test-naming`, in `fast`, `full` and `staged`) and the
  eleven are placed: `artifact_record`, `capability_proof`, `catalog_source`, `dispatch_phase`,
  `mirror`, `owned_store`, `repair_brief`, `skill_source`, `spend_calibration`, `surface_report`
  and `ui` each have their own file (basicly-u2hl.14).
- **The gate runs forward only, and says so.** A source unit must have a test file; a test file
  need not have a source unit — `tests/` legitimately covers `.scripts/`, the git hooks, the
  shipped kit and whole-loop integration paths, none of which are modules, and failing on those
  would make the gate unrunnable rather than stricter. The unit is what the package exposes: a
  top-level module is one unit and a subpackage is one unit, so `renderers/claude.py` is covered
  by `tests/test_renderers.py`. A derived name that is already another unit's own test file does
  not count, which closes the hole where splitting a module and deleting its test file would read
  as covered under the very form the split created.
- **`[sizing] working_set_max` raised from 132,000 to 200,000.** The ceiling is derived from the
  dispatch record, not chosen, and `basicly-u2hl.14` itself completed at a re-derived estimate of
  197,646 — a 27-path scope costing 65,882 to read at the feature seed. A second instance of the
  `basicly-tcmy.5` shape by a new route: this lane did not widen its scope, it *wrote into* the
  scope it was admitted on, being a gate whose deliverable is new test files under paths its own
  scope already named. `src/basicly/config.py` carries the derivation and what the number is not
  (basicly-u2hl.14).

- **DECOMPOSE and BUILD now hand on a schema-validated artifact, and the next state refuses
  one that does not validate.** The first two of the six handoff artifacts
  `docs/design/factory-loop-requirements.md` §8 specifies, and deliberately only two: §2.1
  accepted a risk on D4 against a recommendation to prove one schema first, and its mitigation
  is to sequence `decompose->build` and let the other four be built to a shape that has
  survived contact. `implementation-plan` (`.basicly/core/schemas/implementation-plan.schema.json`)
  carries, per planned child, the five fields the plan gate already refuses a unit for —
  acceptance criteria, scope globs, declared dependencies, token budget, integrity level —
  resolved onto the ids the decomposition created, plus the parallel groups those ids were
  placed in; `decompose` records it on the feature and the fan-out into BUILD refuses to start
  when it does not validate, naming the failing field and its JSON path
  (`$.tasks[0].integrity: 'L4' is not one of ['L1', 'L2', 'L3']`). `change-summary`
  (`.basicly/core/schemas/change-summary.schema.json`) carries what changed and why, the commit
  and the landing's own self-check verdict; a finished build records it and VERIFY's entry
  refuses a broken one before it spends a check run. Every field of both is **derived** — the
  bead's title, the branch head and changed paths read before the merge, the landing verdict —
  so neither artifact asks a model to satisfy an output contract, which the research found is
  the least standardised element in this field.
- **Where a handoff artifact is stored, decided.** D13 resolves storage as typed events in the
  owned ledger; this reaches that through `br.add_comment`/`br.read_comments` as a
  `[harness-artifact]` marker rather than by appending to `.basicly/ledger/` directly. A new
  event kind would have no writer while the repo runs `[tracker] mode = "external"`, whereas
  the marker seam writes on every rung and becomes a ledger `comment` event at the flip; and a
  direct ledger append would leave dirt the advance cannot sweep (it commits only `.beads/`),
  wedging the very landing the artifact exists to gate. So `basicly-u4xu` and `basicly-vkh0.23`
  are no longer prerequisites of §8. Measured bound: below `owned` the marker is one argv
  element and Windows caps a command line at 32,767 characters, against 21,890 for this repo's
  largest real decomposition — it fails loudly if a plan ever crosses, and the ceiling
  disappears at `owned`.
- **Both ends of the contract turn on together.** The schemas are catalog sources, so a repo
  that has not installed them writes no artifact and refuses none — the producer and the
  consumer each resolve the schema before anything else, which is what keeps a skipped write
  from becoming a refusal one state later. A unit carrying no artifact is likewise admitted:
  the gate binds on the marker its own producer writes, so a feature decomposed before this
  existed still builds, and only a present-and-invalid artifact is a defect (basicly-u2hl.18).

- **Every unit of work is assigned an integrity level, by a deterministic rule over its declared
  paths.** Three levels, and the level — not a judgement and not a prompt — selects the gate set,
  the model tier and the rework allowance a package earns, read from one record rather than
  re-derived per caller. L3 is the five consumer surfaces the semver freeze names (the CLI,
  `basicly.toml` and its overlay, the catalog source schemas, the generated-file/manifest
  contract, the owned ledger format); L1 is docs and tests; everything else, including a path the
  rule has never been taught, is L2. The rule is total and single-valued: every path a repo can
  hold resolves, and no path is claimed by two clauses. Because a path rule alone over-classifies,
  a change to a consumer surface that is under the configured line threshold and alters no public
  signature is downgraded to L2 with the reason recorded. Classify assigns the level from the
  scope it is given and records it as a `[harness-classification]` comment, so the verdict travels
  with a clone (basicly-u2hl.2).

- **The plan gate refuses a planned child that cannot name its end-to-end demonstration.**
  Every child in a plan now declares a sixth field, `demonstration`: how it is exercised through
  the consumer surface — a command to run, a request to make, or a test — with the runnable part
  in backticks. A child that names none is refused at plan time by `basicly decompose --plan`, by
  `--children`, and by the loop's child-plan proposer, naming the child; so is one whose
  demonstration is prose naming nothing runnable, on the same rule that already refuses a `## Scope`
  entry that is not a backticked glob. A child with no consumer-visible behaviour is a horizontal
  slice, and a horizontal slice leaves verify nothing to derive a check from — the refusal moves
  that discovery to the point where splitting the plan is still cheap. The field is recorded in the
  child's `## Plan` section and reads back with the rest (`basicly-u2hl.20`).
- **`basicly.plan_entry`** now holds the build-entry predicate that decides whether a recorded bead
  may be dispatched (`build_entry_verdict`, `entry_verdict_for`, `EntryVerdict`), split out of
  `basicly.plan_gate` along the boundary that module's docstring already drew: judging a proposed
  plan against reading a recorded one back. It deliberately does **not** require a demonstration —
  every bead recorded before the field existed carries a `## Plan` heading without one, so on that
  population its absence cannot be told from a defect (`basicly-u2hl.20`).

- **BUILD's downstream-WIP entry predicate now exists and binds.** Requirements 3.1 states
  BUILD's entry condition as *plan gate green **and** downstream WIP below limit*, and only
  the first half was implemented: `[worktree] concurrency` bounds how many lanes run at once,
  and nothing bounded how much finished-but-unlanded work piled up behind them. A supervised
  pass that landed five lanes faster than anyone reviewed them produced five lanes' worth of
  unreviewed surface, and neither the spend ceiling nor the concurrency cap could see it —
  the quantity that actually runs out is review capacity, counted in units rather than
  tokens. `[policy] max_downstream_wip` (default 5) is that bound: `basicly.wip` counts the
  session's units parked in `verify` or `ship` — the same population `advance_parked` drains
  each pass, so the bound cannot wedge — and a pass starts only what the remaining headroom
  admits. Lanes past it are returned unstarted and `refused`, so they route to the decision
  queue rather than burning a rework attempt, and each says which limit holds it and which
  units to land to clear it. Reported on every pass, refused or not (`wip: 2/5 unlanded
  downstream of build; …`), because an unbounded pass must never again look like a checked
  one; a pass the bound holds entirely also queues an escalation on the session root, so a
  client reading only the queue does not see it as an idle pass (basicly-u2hl.23).

- **The Hold and Kill gate verbs now do something.** Every escalation the supervisor raises has
  always offered `park` as a route and nothing anywhere carried it out, so an operator who parked
  a lane watched the next pass dispatch it again; `kill` had no surface at all. Answering an
  escalation `park` (or `hold`) now sets the lane `deferred` and records the reason on the bead,
  which is what makes `loop_state.is_dispatchable` refuse it and stops it holding its parent open
  — so it is human-only, like `land anyway`, and a delegated answer says plainly that it parked
  nothing. New `basicly loop kill <id> --reason "<why>"` tears the lane's worktree down and closes
  the bead won't-do-this-way. Run bare it refuses, mints a one-time code and writes nothing: kill
  is the only verb that removes a *requirement*, so a human is required at every integrity level
  and neither an autonomy grant nor an interactive terminal substitutes for the relay. The
  teardown runs before the close, so a refusal can never leave a closed bead bound to a live
  worktree, and committed work is left on the `harness/` branch unless `--discard` is passed.
  The requirements document's §5 blamed this on the status vocabulary — `deferred` was already
  excluded from `DISPATCHABLE_STATUSES` — and now records the correction with the real gap
  (basicly-u2hl.3).

- **A failed lane is repaired in its own worktree, briefed with the findings that rejected it.**
  Rework used to dispatch a fresh agent with the same fixed prompt every attempt — the same tier,
  the same framing, and no knowledge of why the last attempt failed — so a lane spent its rework cap
  without changing a variable. `basicly.repair_brief` assembles the actual gate evidence (the check,
  the command, the result) and `loop` dispatches the implementer in **repair mode** into the
  worktree that already holds the work.

  Repair is a mode of the implementer rather than a new role: it differs in prompt alone, not in
  tier, tools or artifact, so it maps to `implementer` and the mode travels in the brief
  (`basicly-u2hl.4`).

- **Module size is now gated as a token ratchet, and cyclomatic complexity is linted.**
  Nothing in this stack measured module length — ruff has no rule for it — so `cli.py` reached
  53,095 tokens with every gate green. The new `module-size` check (`.scripts/check_module_size.py`,
  wired into the `fast` and `full` verify sets, so it runs at commit time) measures every tracked
  `.py` under `src/`, `tests/`, `.scripts/` and `.basicly/core/` in tokens and refuses one that
  crosses `decompose.SCOPE_FILE_READ_CAP` — imported, never respelled, so the size a lane is
  refused at is the size the sizing governor budgets with. It is a ratchet rather than a hard cap:
  the 78 modules already over the cap are recorded at their go-live counts in
  `[tool.module_size.frozen]` and may only shrink, an entry that reaches the cap is deleted rather
  than lowered, and a deliberately cohesive module may carry a one-line `module-size-waiver:`
  reason whose count is itself ratcheted in both directions. Read it as an agent working-set gate,
  not a defect-density claim — the defect literature argues the other way, and the gate's docstring
  says which studies must not be cited in its support. Separately, ruff now selects `C90` with
  `max-complexity = 15`, measured at 0 violations on the tree it landed on and 14 at 10, so it
  binds the next function that crosses instead of arriving with a backlog and an argument
  (basicly-u2hl.5).

- **`validate` is a real loop phase**, sequential after `verify` and before `ship`, with its
  own handler and an entry in `[policy.evidence]`. Its gate, `validate-as-consumer`, binds
  only where a unit's recorded `[harness-classification]` marker names L3 — L1 and L2 cross
  the state in the advance they always did, and a unit carrying no marker is unaffected, so
  work already in flight neither gains a rung nor is refused. A unit resting in `validate`
  counts against the downstream WIP bound.

  Two supporting fixes this rests on. The `verify` and `ship` rungs are now derived from the
  per-gate fields of `GateStatus` rather than the aggregate `can_advance`, so requiring a
  second gate no longer drops a merged unit back to `build` and re-runs a landing that
  already succeeded. And intake now passes the bead's declared `## Scope` to `classify`,
  which it never did: `integrity.assign(())` hit its `unclassified` fallback, so **every
  unit the loop had ever classified was recorded L2** and no L3-gated behaviour could fire.

  The advance out of `validate` now refuses on a failed or missing consumer gate, and the two
  refusals are different. A `validate-as-consumer` result recorded **failed** by an engine
  provider spends one bounded rework attempt through the existing `_rework` path and escalates
  into the decision queue at `max_rework`. A **missing** result blocks without spending an
  attempt — nobody has looked yet, so there is no finding to repair, and charging it would burn
  the budget that exists for repairing findings and then escalate a unit whose validation had
  never run. A result whose provider is outside `ENGINE_GATE_PROVIDERS` still leaves the gate
  missing, but is now named in the refusal rather than silently ignored. Neither refusal merges,
  tears down a worktree, closes the bead or commits tracker state.

  VALIDATE now dispatches the `validator` role. The dispatch resolves its persona through
  `roles.resolve_role` exactly as the repair dispatch does, falls back to the default runner when
  the family cannot load the role rather than emitting a flag the host would drop, and runs in the
  base checkout because a consumer exercises the merged product rather than the branch that made
  it. It is metered like any other dispatch, so it binds the spend ceiling as a fifth site. When it
  returns, the engine re-reads the gate instead of assuming a verdict was recorded — a dispatch
  that recorded nothing leaves the unit resting in `validate`.

  A validate dispatch is now recorded under `run_record.VALIDATE_PHASE` rather than `BUILD_PHASE`.
  Every dispatch through `loop._run_agent` was previously labelled a build, which would have put a
  read-only judge's cost into the write-dispatch sample the spend calibration prices a lane from.

  Two extractions the size and density ratchets forced, both real seams: `dispatch_brief` now holds
  the prompts the loop dispatches with, and `landing_gate` holds the reading of an answered gate
  escalation and what it authorises. `landing_gate` carries a stated `comment-density-waiver` — its
  four functions are small and their docstrings are the incident history that makes them correct.

  The verdict is recorded by the engine, not by the validator. `br gate report` requires
  `--provider` and authenticates nothing, so an agent told to report its own gate would
  either error and record nothing — leaving the unit in `validate` forever while believing
  it had reported — or self-certify a required gate. The validator now ends its reply with
  `VALIDATION: PASS` or `VALIDATION: FAIL` and the engine writes the result under its own
  provider.

- **The loop originates the work type and the child plan instead of waiting for one.**
  Under a grant whose level permits it (L2+), `loop advance` dispatches a corpus-bounded,
  tool-confined proposer for the input a phase needs, validates what comes back against the
  same plan schema and working-set band `basicly decompose` already enforces, and continues
  through the phase. No grant, an unconfinable runner, or a proposal that fails validation
  falls back to the existing needs-input block, naming which input is missing and why nothing
  was proposed. `basicly policy grant` now reports the two authorities separately —
  `approves checkpoints:` and `originates proposals:` — so a level that approves the decompose
  checkpoint but may not propose the plan says so (basicly-u6jq.2).

### Changed

- **A lane declares a verify check and a ratchet delta in its own file.** `basicly.d/<bead-id>.toml`
  now carries a lane's `[[verify.checks]]` entries and its `[ratchet.<gate>]` contributions, and
  `basicly verify`, the pre-commit hook runner and the two ratchet gates all assemble the
  fragments on top of `basicly.toml` and `pyproject.toml`. Every ratchet number in a fragment is a
  delta rather than a total, so lanes compose by addition in any landing order. Appending to those
  two shared anchors bounced three of five lanes on the 2026-08-08 pass; the collision is now
  impossible by construction rather than detected, as `changelog.d` already made it for
  `CHANGELOG.md` (`basicly-ef7t`).

- **The harness's `[harness-*]` markers are carried as owned-ledger events instead of `br`
  comments.** Step 5 of `docs/design/work-tracker.md` §5, and the step that actually removes
  `br` from the engine rather than merely making it non-authoritative. `comments` was the
  largest remaining dependency — 26 of the engine's 55 `br` call sites and 45% of all recorded
  tracker traffic — and measured over the live tracker, 1646 of its 1834 comments (89%) are
  harness markers using a beads comment purely as transport: checkpoint approvals, gate
  records, grants, rework counters, needs-input, the human-wait clock, dispatch records and
  spend rollups. Three new seams carry them — `br.add_comment`/`br.try_add_comment` to write,
  `br.read_comments`/`br.try_read_comments` to read one bead, `br.all_comment_texts` for the
  whole-tracker evidence read — and with `[tracker] mode = "owned"` each answers out of the
  event log under `.basicly/ledger/` with no `br` spawned at all. The two contracts are
  deliberately split: a counter or a refusal reads the hard function, which raises when the
  store cannot answer rather than reporting "no markers" and letting the loop advance past the
  gate the marker existed to hold, while an idempotency or telemetry read takes the soft one.
  The read-only ban that a pre-flight gate runs under is now enforced at the seam itself, so it
  still refuses a marker write on the rung where there is no `br` call underneath to inherit it
  from. Below `owned` nothing changes: the write still goes to `br` and the dual write still
  mirrors it. The 188 human comments are untouched — a human writing prose runs `br` directly
  and the engine never spawns that. Two `comments list` spawns remain at their own call site
  (`decompose`'s sizing markers, `supervise`'s found-info records), each writing and reading
  the same store; retiring them is `basicly-wpc8` (basicly-s5li).

- **The ruff rule families the stack was leaving off are enabled, and security lint now reaches
  `src/`.** `TRY`, `PERF`, `FURB`, `A`, `RET`, `TC`, `TID`, `DTZ` and `BLE` are adopted; `S` is
  adopted over `src/` and per-file-ignored elsewhere so bandit keeps the trees it already scans.
  `TRY003` and `TC003` are deliberately ignored with the reason recorded in `.ruff.toml` — style at
  scale, not a defect class — and `S101` mirrors the existing bandit `B101` skip rather than
  inventing a second answer.

  **Consumers inherit a stricter gate.** The change is called out here rather than filed as a chore
  because a repo that installs basicly gets these families on its next upgrade. What made `S` over
  `src/` worth the churn is measurable: `src/` carried 21 `# nosec` comments that no scanner read —
  bandit was configured over `.scripts`, `.basicly/core/hooks` and `.basicly/core/kit` and never
  `src/` — including an `autoescape=False,  # nosec B701`. An inert suppression reads as "reviewed"
  and is not; 21 of the 25 findings landed on exactly those sites (`basicly-u2hl.11`,
  `basicly-u2hl.16`).

- **The tier injection kit moves into its own directory.** `.basicly/core/kit/` now holds one
  directory per kit — `tier/` beside `tracker/` — instead of one foldered kit and three loose
  modules. **Breaking for an installed consumer**: a Claude settings hook written by
  `install_hook.py` names the old path and stops resolving until the installer is re-run from
  the new location, `python3 .basicly/core/kit/tier/install_hook.py --user`. The directory is
  not cosmetic — `kit-deployment` and `kit-boundary` scope themselves by it, so the three loose
  modules had no gate looking at them.

### Fixed

- **A dispatched classify, decompose or lane run now carries the persona its phase declares.**
  `resolve_role` had exactly one caller, inside `_run_agent`, whose only call sites are build and
  repair — so the two proposal dispatches and the supervised lane dispatch all ran on the default
  runner unspecialised, and no recorded dispatch had ever reached an argv with `--agent` on it.
  The work-type proposal now resolves classify's persona, the child-plan proposal decompose's, and
  a lane build's. The phase is passed per call site rather than derived from the proposal's label,
  so a third proposal cannot silently inherit no persona; and resolution still answers None for a
  family that cannot select a role, so an un-upgraded consumer gets an unspecialised loop rather
  than a flag its host would drop without a word.

- **A landing no longer silently discards a lane's merge resolution.** `git rebase` skips merge
  commits unless `--rebase-merges` is passed, so a lane that resolved a conflict with
  `git merge` — producing content held in neither parent — had that content deleted while the
  rebase reported success and exited 0. It happened twice in one session here, and on one of
  them the test suite stayed **green** afterwards, because the feature and the tests covering it
  were dropped together: a consistent tree that no longer did the thing it shipped. No gate can
  catch that shape, because nothing is left to fail. The merge queue now refuses such a branch
  before rebasing it, naming the merge commit and telling the lane to linearize; the lane keeps
  every commit it had. A second guard compares the tracked paths either side of the replay and
  restores the branch if anything was lost to a cause nobody enumerated, so the queue can no
  longer both drop work and report success.

- **A lane that adds a config key can now declare it in the same commit.** A repo's
  `basicly.toml` is validated against the `CONFIG_SCHEMA` that repo's *own tree* ships, not
  against the schema of whichever engine happens to be running. This unblocks the landing:
  `basicly loop advance` runs from the base checkout, so the process validating a lane's config
  is the pre-merge engine, and a single commit that taught `CONFIG_SCHEMA` a name and declared
  it was refused for a key introduced by the code one line away — four times in the field
  (`[worktree] append_only_paths`, `[runner] quiet_after`, `[tracker] mode`,
  `[catalog] rank1_floor`), each time dying before verify ran with a message that read as a
  config typo. The tree's schema is read statically, so nothing imports an unmerged engine, and
  it fails closed: a tree whose schema this reader cannot parse falls back to the running
  engine's and the refusal then names the ordering rule (schema first, declaration next) rather
  than leaving the operator to work it out. The strict refusal itself is unchanged — a checkout
  with no schema change is judged by exactly the schema it was before, and a consumer repo,
  which ships no engine source, is unaffected (basicly-69az).

- **The projected skill listing fits the budget a consumer actually gets.** A host caps each skill's
  `description` plus `when_to_use` at 1,536 characters and budgets the whole listing at 1% of the
  context window; on overflow it drops descriptions **starting with the least-invoked skills**,
  which is a feedback loop rather than a cost — the skills nobody invokes are the first to become
  uninvokable. The listing had grown past a 2,000-token consumer budget. `catalog lint` now gates
  both caps and the listing is back under (`basicly-a3ab.12`, `basicly-u2hl.45`).

- **`AGENTS.md` is back under its size cap, and the check runs from `basicly check` rather than only
  from `build`.** The audit behind it found the overrun was not the always-on baseline: the extra
  characters are the path-scoped tier that claude and copilot receive as separate rules files and
  Codex, which has no glob-based instruction scoping, must inline. Evicting baseline lines would
  have charged all three families to fix one and left the cause standing, so the Codex cap moved to
  16,000 characters instead. What that trades away is stated where the cap lives: it also stood
  proxy for a vendor claim that adherence degrades with length, which this repo has not measured
  (`basicly-a3ab.1`, `basicly-a3ab.10`).

- **A claim an epic's own closed children superseded no longer reaches a decider as a current
  fact.** An epic's `## Context` bullets are the whole authority a delegated decision runs on
  (`decider_contract.intake_corpus`), and they are the one part of a bead nothing revisits.
  Measured on `basicly-u2hl` (2026-08-08): four of eight bullets had been superseded by its own
  closed children and one was refuted outright, after which two escalations quoted the refuted
  bullet verbatim, reasoned from it and abstained to a human — while `git merge-tree` reported
  both lanes already mergeable, so both sat in `build` holding live worktrees. The corpus now
  marks every bullet that names no child of its own bead as `UNVERIFIED — possibly superseded`,
  in place at the head of the claim, because a decider reads top to bottom and a correction
  anywhere else is one it never reaches.
- **A bullet is accounted for by naming a child, never by resembling one.** Attribution by text
  similarity was measured against that same case and refused: TF-IDF over the closed children's
  titles ranked the true superseder first for 1 of the 4 known pairs and scored an unsuperseded
  bullet at 0.50 against an unrelated child; term coverage over their full descriptions reached
  2 of 4 with false pairs at 0.78. So nothing guesses which child killed which bullet — a claim
  either names a child of its own bead (the form the hand correction already used, `SHIPPED
  2026-08-08 (basicly-u2hl.4): ...`) or is marked unverified, and anything else is flagged.
- **The `corpus-drift` verify check reports it before a decider ever sees it.** It reads the
  committed tracker export, so it runs in a fresh clone with no tracker binary, and covers open
  parents with at least one closed child. `--strict` names every unaccounted bullet and exits
  non-zero; the wired gate ratchets against `[tool.corpus_drift.frozen]`, which records the one
  bead already unaccounted for when it landed and may only fall (`basicly-b9ef`).

- **A repair dispatch is now refused when the grant cannot pay for it.** D3's halt predicate had
  three enforcing call sites — delegated approval, supervised lane admission and decider delegation — and
  `basicly-1th1` added a fourth for the interactive build dispatch, but the repair path reached
  `runner.run` past all of them. So a landing that failed a gate briefed and spawned a metered agent
  on an exhausted grant, which is exactly when a grant is most likely to be spent. The spend ceiling
  is now checked before the repair spawns, and the brief is written back on refusal rather than
  consumed, so "no budget" does not turn into "the failure is forgotten". D3's halt was split out of
  the composite refusal for this, because a repair is a second attempt at work already planned and
  already sized and must not be re-admitted against the plan gate or the working-set band.

- **Completing a bead's `## Scope` no longer makes its lane look bigger.** One field was serving
  two gates that want opposite things: the merge scope-collision gate wants the declaration
  complete — every path the diff touches — while the sizing band wants it small, because it prices
  what the declaration *reads*. Declaring honestly for the first necessarily inflated the second.
  Measured inside a single landing on `basicly-u2hl.14`: 13 scope entries estimated 78,709 and the
  merge refused 16 undeclared paths; 27 entries estimated 197,646; 35 entries estimated 245,466 —
  and the diff was exactly as wide throughout. Only the declaration moved, and the working-set
  ceiling was raised twice to let it through, which is a ratchet moved by an artifact rather than
  by evidence. A bead may now declare a `## Working Set` section — the subset of globs the lane
  must actually read, written as backticked globs like `## Scope` — and the band, the dispatch
  forecast and the ceiling derivation price that instead. `## Scope` stays the ownership
  declaration the merge gate reads and the collision graph learns from, complete and free. A bead
  that declares no working set is priced from its scope exactly as before, so nothing already
  authored changes; a ceiling refusal now names declaring one as the alternative to splitting a
  lane that has not grown (basicly-efw2).

- **A role's declared `skills:` now reach the agent dispatched for it.** The field is
  documented and typed, but it is honoured only when a definition is spawned as a subagent —
  under `claude --agent <name> -p`, the shape the engine dispatches with, it does nothing
(probed twice on claude 2.1.231 with a positive control). Five of eleven projected roles
  declare skills, so every one of them ran without its specialism.

  The engine now reads the bodies a role declares and carries them in the dispatch prompt,
  ahead of the task. Measured against the alternative before choosing it: the largest role's
  skills are 3,261 tokens where a lane costs 8–11 million, so this is about 0.03% of a lane —
  and unlike the vendor's own mechanism it reaches codex and copilot too, which matters for a
  harness that advertises three families. A role declaring no skills gets a byte-identical
  prompt. A declared skill with no readable body is named in the prompt rather than logged,
  because the agent is what can act on it by loading the skill itself.

  This also gives `catalog_lint`'s skill/role pairing a runtime effect it did not have before,
  so the lint now enforces something real.

  A claude dispatch record now carries its cache split. `claude_json_usage` and
  `claude_turn_usage` summed the four reported token counts into the total and discarded the
  breakdown, so every claude run record read `cache_read_tokens: null` and no cache-hit ratio
  could be derived from the ledger at all. Claude reports its counts disjoint from each other
  where codex reports `input_tokens` inclusive of the cached portion, so the claude extractor
  folds them to the same provider-neutral convention rather than storing the raw field — which
  keeps `input_tokens - cache_read_tokens` a valid uncached figure whoever produced the numbers.
  A usage block that omits a cache key records null rather than 0, because a turn that really
  read no cache reports a genuine 0.

- **The type checker now analyses the scripts and hooks it had been silently skipping.**
  pyright's default `exclude` is `["**/node_modules", "**/__pycache__", "**/.*"]`, and that last
  pattern dropped `.scripts/` and `.basicly/core/` — including the git hooks that ship to consumers
  via `basicly install` and the kit modules that run in the dispatch path, which is the code with
  the widest blast radius — from every mode this repo runs. Nothing failed, because a checker
  cannot report on a file it never opened. `[tool.pyright]` now spells out both `include` and
  `exclude`: an `include` alone would not have been a fix, since `exclude` is applied on top of it
  and wins, and it filters files named explicitly on the command line too (`pyright
  .scripts/check_module_size.py` analysed 0 files). Coverage goes from 204 files to 242 — the 38
  tracked modules under those two trees — and the four errors that were hiding there are fixed: an
  `ast.AST` walked without narrowing to `ast.expr` before reading `.lineno` (`kit-boundary.py`), a
  `__doc__.splitlines()` on the `str | None` module
  docstring in two argparse setups, and a `modalities.get()` narrowed on a second call rather than
  on the value. `tests/test_type_checking.py` sweeps the whole tracked tree against both lists, so
  the next directory of first-party Python fails there instead of inheriting the silence, and runs
  pyright over a bad module under `.scripts/` to prove the coverage is real — with the config minus
  its `exclude` override as the discriminator, which analyses nothing and exits 0 (basicly-u2hl.15).
- **The spend-accuracy gate now measures a bead, not one attempt at a bead.**
  `forecast_spend_tokens` is derived from a bead's `## Scope`, so every dispatch of that bead
  records the identical number — what getting the whole bead done should cost — while
  `decompose.spend_accuracy` compared it against each dispatch separately. A bead dispatched more
  than once therefore had every attempt after the first scored against a forecast that covers work
  an earlier attempt already did, which is a structural under-spend rather than a forecast error:
  `basicly-u2hl.14` ran 30,139,416 then 2,785,270 then 1,512,403 tokens against a 26,320,290
  forecast, and the third attempt alone read as 0.057x and turned `main` red while the lane itself
  came in at 1.31x. The same unit error as basicly-tcmy.34, one level up — a number held against a
  quantity it does not denominate. A bead's comparable dispatches are now summed into one
  `SpendPair`, the forecast taken from the latest of them (a re-dispatch re-reads the bead, so four
  of the eight multiply-dispatched beads in this ledger carry forecasts differing by 2.5-9.7% across
  their attempts; each lands in band under either end, so no verdict turns on the choice), and
  `attempts` is carried on the pair and named in the violation — "spent
  17,000,000 tokens over 4 dispatches" — so an aggregate can never read as one runaway lane. The
  live gate goes from one violation to none across 60 lanes, and an overrun spread across four
  dispatches still fires (basicly-u2hl.15).

- **The `chars/4` token estimator is constrained to prose, and the scope reader stops parsing
  binaries as text.** The estimate is calibrated on English and is wrong by a wide margin on
  anything else, which fed a sizing governor that decides whether a unit of work earns a lane at
  all. It now applies where it is valid and reports rather than guessing where it is not.

  The tokenizer itself deliberately stays `chars/4`: a real one fetches a 3.5 MB vocabulary over
  HTTPS on first use, which a consumer's git hook cannot do. That is a decision with its error band
  recorded rather than an unmeasured default (`basicly-u2hl.32`, `basicly-ca42`).

- **Answering `park` on a stalled lane now parks it.** Five question shapes across
  `policy.rework_escalation_question` and `supervise._capped_dispatch` offer that route, but the
  carrier accepted the answer only from a decision of kind `escalation` — so an operator who parked
  a stalled lane saw `answered <id> by human`, the bead stayed `open` and dispatchable, and the next
  supervised pass ran it again. The carrier now binds on the `or park?` suffix every one of those
  questions ends with, so it cannot accept a route its producer offers and then drop it. Answering
  `park` on a question that offers no routes still holds nothing, and a delegated answer still
  cannot park a lane.

  Corrected the `--resume --fork-session` economics recorded in the requirements and the
  implementation plan, re-measured on claude 2.1.231. The mechanism stands; two figures a lane
  would have been sized against did not. The 19x headline is denominated in the ~21,800-token
  host floor rather than a repo corpus, so corpus reuse is nearer 10x. And the cross-directory
  penalty is one-time **per working directory**, not per fork — a first fork into a fresh
  worktree reads 74–87%, every later fork into that same directory reads 100% — so the earlier
"5.4x degradation" was a first-fork measurement read as a steady state. Whether
  `--exclude-dynamic-system-prompt-sections` composes with `--agent` is now recorded as
  unestablished rather than inferred: that probe was confounded by arm ordering, which a
  position control demonstrated.

## v0.8.0 - 2026-08-07

Delta: v0.7.1..v0.8.0

### Added

- **`basicly loop supervise --label LABEL` fans a pass out over the beads carrying a label,
  instead of the root's parent-child children.** A release cut is assembled from work that
  already exists, and `br` permits exactly one parent, so every bead in a cut already has an
  epic of origin. Parent-child fan-out therefore could not express a release at all: the root
  could gate the work as `blocks` dependencies — enough for the autonomy grant, which walks
  both edge kinds — and still seed none of it, because seeding walks descent only. A cut drawn
  from existing epics could not be one pass.

  With `--label`, membership is a tracker query rather than a graph edge. The root keeps the
  three jobs it is genuinely good for — anchoring the grant, the singleton lock and the
  decision queue — and stops being the thing that decides which beads are in. Nothing is
  re-parented, so a bead's epic of origin survives being included in a cut, and the same bead
  can appear in a later cut under a different label.

  This is the selector `[policy] phase-*` labels and `br list --label` already implied: phase
  membership has been a label since the plan stopped listing bead ids, and this makes the
  supervisor read membership the same way (`basicly-1lpo`).

- **The append-only event log the owned tracker is built on.**
  `.basicly/core/kit/tracker/events.py` is the store the rest of the kit derives from: a
  record's state is a **fold over its events**, so history lives in the data rather than
  depending on git history surviving a squash or a shallow clone, and everything else — the
  snapshot, any index — is derived and disposable.

  The fold is a function of the event **set**, not of file order: a shuffled log, a
  concatenated one, or the same events split across files all fold to the same state, which
  is what makes a union merge safe. Event ids are content-derived, so a duplicate arriving
  from a merge cannot change the result. Sequence numbers from the single writer give total
  order.

  **A wall-clock timestamp is evidence and nothing branches on it.** That is the rule, not a
  preference: the clock defect this kit exists partly to escape cost two tracks of
  workaround, and the log's own ordering must not inherit it.

  An unknown event kind is **counted and reported, never folded and never an error**, so an
  older reader meeting a newer ledger degrades rather than refusing. A *known* kind carrying
  a payload it cannot mean is refused, because silently skipping it would fold a record to a
  state no event ever wrote (`basicly-vkh0.11`).

- **Opaque record ids sized against a declared collision probability, and derived ids for
  evidence.** `.basicly/core/kit/tracker/ids.py` mints a record id from an explicit
  collision budget rather than from a guess at how long is long enough: the length follows
  from the birthday paradox against a stated maximum probability, and it is **adaptive** —
  which is safe precisely because existing ids never change.

  An id is opaque. Nothing in the harness may parse one to recover meaning from its text,
  because an id whose characters carry information is an id that cannot be reissued, and a
  prefix-anchored gate has already truncated a slug-shaped one.

  Evidence ids are **derived from content** instead of minted, so the same fact recorded
  twice is the same id and an idempotent write is idempotent by construction rather than by
  a caller remembering to check (`basicly-vkh0.12`).

- **The owned tracker's record snapshot: a fold you can keep, and prove stale without
  folding.** `.basicly/core/kit/tracker/snapshot.py` derives every record's state from
  the event log and writes it as a local, gitignored artifact anybody may delete. Its
  first line carries the log's tip event id and line count, so a reader dates the
  snapshot by *scanning* — counting newlines and decoding one line — instead of folding.

  Two details are what make that safe rather than merely fast. The recorded id is the
  log's **tip**, the last line of the last file, not the canonical maximum: canonical
  order sorts by record then sequence, so its maximum is the highest *record id*'s last
  event, which no cheap read can find. And the scan is taken **before** the fold, never
  after — scan-first under-reports and reads as stale, while scan-second would claim to
  have folded a line it never saw and read as *fresh*. The invariant is one-directional:
  a cheap check may say stale when it is fresh, and may never say fresh when it is stale.

  Rotation is by period and **archives everything, pruning nothing**, because folding the
  whole history is a requirement. It writes one new empty file whose name sorts last,
  which is all the append target looks at, and publishes a checkpoint carrying every
  item's totals — including an item idle since before the boundary, which is what bounds
  steady state to one checkpoint plus the current file. The period is an argument, never
  a clock read.

  Nothing repairs a derivative: an unparseable snapshot or checkpoint is replaced from the
  log, because a repaired cache is a second source of truth wearing a green tick
  (`basicly-vkh0.14`).

- **`fsck` and `rebuild` make "the log is the truth" a claim you can check.** Without a
  check that folds the whole log and reports what it finds, and a rebuild that regenerates
  every derivative from the log alone, that sentence is untestable. Both now exist in
  `.basicly/core/kit/tracker/fsck.py`, and the report sorts its findings by **what fixes
  them** rather than only saying *bad*:

  - a defect in the **log** — `rebuild` cannot touch it, because repair is by appending a
    corrective event and never by editing a line, or the checker quietly becomes an editor;
  - a **derivative** that disagrees with the log it claims to summarise — fixed by
    replacement;
  - a **warning** for an event kind this version folds no state for, never a failure, so an
    old reader meeting a newer ledger does not report false corruption.

  A *stale* derivative is deliberately not a finding: every reader regenerates on a stale
  read, so lagging is the design working. The case that matters is the one the cheap check
  cannot reach — a derivative whose header agrees with the log and whose **body** does not
  — so that is the one place a fold is spent on a derived file.

  Two findings are suppressed rather than reported, both for one reason: a forked record's
  carried totals are void until a fold restates them, so a totals disagreement there is the
  fork's consequence, not a second defect. Reporting the consequence beside the cause is how
  a report of eleven findings hides its one root (`basicly-vkh0.15`).

- **A `kit-boundary` gate that can actually see the kit tree.** The portable kit under
  `.basicly/core/kit` has one structural rule — the engine imports the kit; the kit imports
  nothing, and never reads basicly's config loader, logging, session state or policy module.
  `docs/requirements/work-tracker.md` §4 named `lint-imports` as the enforcement for it. That was
  unenforceable rather than merely unimplemented: import-linter analyses the `basicly`
  package, and the kit is flat modules with no `__init__.py`, outside it and not on
  `sys.path`, so the tool never opens a kit file. Measured, not argued —
  `test_import_linter_cannot_see_a_kit_violation` seeds `import basicly.config` into a kit
  and records `lint-imports` reporting `2 kept, 0 broken` while the new gate fails on the
  same line.

  `.basicly/core/hooks/kit-boundary.py` parses every kit module and reports four routes back
  into the engine: a static import, a dynamic one (`importlib.import_module`, `find_spec`,
  `__import__`), a path into the engine's source tree, and a read of `basicly.toml` or of a
  `.basicly/` directory outside the kit's own `.basicly/core`. Path expressions are folded
  first, so `Path(".basicly") / "usage"` is seen. It is wired twice on purpose: as a
  `[[verify.checks]]` entry in `--mode full`, which is what CI runs, and as a `pre-commit`
  hook — the wiring that ships with the kit, so a consumer repo gates the boundary at commit
  time without declaring anything (`basicly-vkh0.16`).

- **The existing tracker imports into the owned log, with deletion as a first-class event.**
  `.basicly/core/kit/tracker/migrate.py` reads a `br` JSONL export and writes the events it
  implies, stamping every one with its provenance and the source snapshot's name, and each
  `created` event with the export's sha256 — so a later reader can say which snapshot a
  record arrived from. That digest is what the shadow differential's sharpest refusal checks.

  **An upsert-only export cannot express a deletion**, which is why tombstones are a
  first-class concern rather than a detail. A record the snapshot merely omits is *reported
  as absent*, never deleted; a deletion has to be **stated** by the caller, and it becomes a
  `tombstone` event carrying the same provenance as any other. A tombstone is refused for a
  record the snapshot still asserts, so a deletion arrives as a later import whose text no
  longer carries it.

  A record the log already holds is not created again, and a divergence between what the log
  holds and what the snapshot says is reported rather than overwritten — the import is a
  translation, not an authority (`basicly-vkh0.17`).

- **A shadow differential that refuses to compare the owned tracker against a copy of
  itself.** The owned event log answers the three queries the loop advances on — phase
  derivation, the ready set, gate status — and those answers are compared record by record
  against the tracker still authoritative for them.

  The load-bearing half is the **refusal**. The comparison must run against the live
  tracker and never against a re-import of its own export, because two derivatives of one
  lossy snapshot agree with each other and prove nothing — and the failure mode is a
  *clean report*, so it has to be something the harness declines to run without. The
  reference side is therefore audited rather than trusted, on three routes: the sha256 of
  the bytes it read against the digest the import recorded; any export at all, on the
  measured ground that a `br gate report` row is visible to `br gate list` and **absent
  from the JSONL export entirely**; and a perturbation probe for a source that declares no
  snapshot, which a genuinely live source ignores and so cannot false-fire.

  `clean` and `conclusive` are separate properties, and a caller cannot get the second by
  asking for the first. A comparison over a population where every record gives the same
  answer has discriminated nothing, which is not hypothetical: before the dual write, every
  bead reported zero gate rows, so that query was constant and a report saying only *clean*
  would have been reporting the absence of evidence as agreement.

  Both sides supply the same view type and the verdicts are derived **once** for both, so a
  disagreement is about a fact rather than about two copies of a rule (`basicly-vkh0.18`).

- **`[tracker] mode` puts the br seam on a rung of the work-tracker cutover: `external`,
  `dual`, or `owned`.** `dual` mirrors every write the engine makes through
  `basicly.br` onto the kit's owned event log under `.basicly/ledger/`, with `br` still
  authoritative for reads. `owned` flips `br.read_record` — the one record-read seam — to
  answer out of that log, while `br` is still written, because the other ten subcommands
  the engine spawns still read out of it. Repos that declare nothing keep the pre-cutover
  behaviour exactly: no ledger is created and the kit is never loaded.

  A write the ledger cannot record **fails the command** rather than warning. Two stores
  are only worth running side by side while they hold the same facts, and the moment a
  missing mirror is cheap to fix is before the next write lands on top of it — so a br
  write with no owned-ledger translation, an `update` flag with no mapped field, and a
  ledger that refuses the append are all errors at the call site.

  The dual write is also the writer `differential.KIND_GATE` was defined for. The JSONL
  export carries no gate field at all, so the import step had nothing to load and the
  shadow differential reported *inconclusive* on the gate query for every population it
  could build — clean, and unable to say that clean meant anything. With `gate report`
  mirrored, a run over a population built through the seam comes back clean **and**
  conclusive, which is the condition `docs/requirements/work-tracker.md` §5 step 4 licenses the
  flip on (`basicly-vkh0.19`).

- **The owned scheduler ranks the ready set with a pure score that reads no clock.** A repo
  on `[tracker] mode = "owned"` now takes its dispatch order from the kit's
  `tracker/scheduler.py` instead of `br scheduler`, through `br.read_ranking` — the
  ranking's own seam, the shape `br.read_record` already has for a record. The ordering is
  `priority ASC, dependents DESC, id ASC` over the ready set: `br`'s fallback policy sorted
  by `created_at`, which made dispatch order depend on when a ledger happened to be written
  rather than on the graph. Nothing on the ranking's input carries a timestamp, so the same
  work graph ranks identically however long it has been sitting there (`basicly-vkh0.20`).

  The score is one integer holding both terms, and `scheduler.explain()` decodes it back
  into "P0, three dependents" — so a dispatch marker recorded months ago stays readable
  without the graph it was computed over. Each answer names the policy that produced it
  (`schema: basicly.scheduler.v1`), which is what tells a rank recorded under the owned
  scorer from one recorded under `br.scheduler.v1`.

  Two things a consumer will notice if they flip. The dependent count is over **blocking
  edges to still-live dependents** only, so a `related` dependent and a closed one both
  count for nothing. And the owned ranking has an opinion where `br` had none: `br scheduler`
  recommends only unclaimed work, while this ranks every ready record, `in_progress`
  included. Repos on `external` or `dual` are unaffected — `br scheduler` still answers, with
  its own schema and sort recorded exactly as before.

- **A new `kit-deployment` gate enforces the two host rules the tracker kit needs, instead
  of stating them in a docstring.** Both were required in prose and satisfied nowhere:

  - the event log must be declared `-text`, or a normalising checkout rewrites bytes whose
    event ids are **content-derived** — so a rewritten byte is a changed id, and every later
    id with it;
  - the ledger's derived files must be ignored, or a fold of the log gets committed beside
    it and recreates the dual-store failure the event log exists to escape.

  The gate reads the log glob and the derived-file patterns off the host's own kit rather
  than spelling them a second time, asks **git** what it does with sample paths
  (`check-attr`, `check-ignore`, `ls-files`) rather than reading the config text, and fails
  naming the rule the host lacks and the exact line to add. `--repo` points it at any
  checkout, so a consumer can check its own.

  This repo's ignore rules name the two derived files individually rather than ignoring the
  ledger directory: `.basicly/ledger/` is a *committed* directory, and a rule that swallowed
  the log would delete the truth to save a cache (`basicly-vkh0.21`).

### Changed

- **A lane is now bounded by what it spends and whether it is alive, not by the clock.**
  `[runner] runner_timeout` was the only terminal bound the engine had, and it is the one
  signal that says nothing about whether work is happening — so it had been calibrated
  inside the upper tail of real work, killing lanes that were fine: the longest
  *successful* lane on this repo's ledger ran 1712s against an 1800s cap, 95.1% of it,
  with 10 of 68 successes finishing past 80%. Two bounds replace it, both read off the
  per-turn event stream every metered dispatch already emits (`basicly-rupz`). A new
  `[runner] quiet_after` (default 1800s) kills a dispatch whose stream has gone silent,
  which is proof of a wedge in a way an unchanged worktree never was — an agent thinking,
  or waiting on a long test run, writes no file but still emits. And the D3 grant ceiling
  now binds *during* a dispatch rather than only between passes: `spend_status` was read
  before a pass and written after it with nothing in between, which is how a 20,000,000
  token grant was overshot to 22,164,783 by lanes that were still in flight when the check
  ran. `runner_timeout` stays, terminal, moved back to its 3600s default and demoted to a
  backstop for what neither new bound can see — a process that hangs holding the pipe, or
  a stream that stops while the process does not exit. A killed lane's run record now names
  which bound stopped it, so `quiet_after` — declared without a measurement, because until
  now the stream was paid for and discarded — can finally be calibrated against evidence
  rather than re-declared (`basicly-lpsf`).

- **A bead is read through one seam with one absence contract.** `basicly.br` was already
  the only place that *spawns* `br`, but not the only place that *reads* it: the
  single-record unwrap was written out at **eleven call sites across eight modules**, and
  they disagreed about failure four ways — two raised, two returned `None`, four returned a
  local empty, one carried a typed absence. A twelfth site guarded the payload shape not at
  all and would have raised `AttributeError` on a non-object payload.

  Now there are two functions and one rule. `br.read_record` returns the record or `None`
  for every way a read comes back without one — `br` absent, a spawn that raises, a non-zero
  exit, output that is not JSON, an empty array, a payload that is not an object.
  `br.require_record` is the hard half and raises **one** message naming the bead, so a
  caller no longer has to know whether it is looking at a missing bead, a missing binary or
  a malformed payload to say what went wrong.

  A tree guard fails the build if any module outside `basicly.br` writes the unwrap again —
  the same reason one reader exists for both of `br`'s dependency spellings. It matches the
  unwrap *expression* rather than any list check, because a plain shape guard on JSON is not
  the defect.

  This is what made the tracker cutover a change to one function instead of eleven
  decisions: the replacement chooses what "not found" means, and an empty list is the
  natural in-process answer — which against the old eleven would have split six sites from
  five, at runtime, across eight modules (`basicly-tcmy.14`).

- **`br` is still required. This release owns the work *store*, not yet the floor.**
  The kit's append-only event log is complete and checkable — provenance on every
  edge, collision-budgeted ids, a derived snapshot with rotation and a staleness
  header, `fsck` and `rebuild`, import with tombstones, and a shadow differential
  that refuses a comparison against a re-import of its own export. `[tracker] mode`
  puts a repo on a rung of the cutover.

  What it does **not** do is remove the `br` binary from what a consumer needs
  installed, and an earlier draft of the roadmap said it did. `owned` flips
  `br.read_record` — one seam — while 44 further spawn sites remain, 26 of them
  `comments`, which is the carrier for every checkpoint, gate marker, grant and
  rework record. Measured with no `br` on `PATH` and the flip forced on:
  `policy.gate_status` and `policy.definition_of_ready` both still raise
  `br is not on PATH; the harness requires the beads tracker`.

  So install `br` as before. The claim that you will not need to is carried to
  `v1.0.0`, whose acceptance test drives a fresh consumer repo with no `br`
  through one unit of work to a landed commit — a test that can fail, rather than
  a sentence in release notes that cannot (`basicly-vkh0.22`).

### Fixed

- **The tracker design's speed argument quoted a p95 as a typical call, overstating the
  advantage of owning the tracker by ~90×.** It cited 113 ms for one `br` CLI read and
  concluded an in-process read was "~175× cheaper". Both numbers were wrong.

  Re-measured from this repo's own committed call ledger — 1,420 recorded engine calls to
  `br` — the median is **14.2 ms** and 113 ms is approximately the **p95**. And the 175×
  ratio compared that p95 against a *single-record* read of a ledger a third of today's
  size: the slow end of one distribution against the fast end of another.

  Held to one comparison at a time, against the live 2.30 MB / 642-record ledger: a full
  fold is **~1.9×** cheaper than the median call, and a single-record read **~15×**. A fold
  is O(events) while a spawn is roughly constant, so the fold ratio *narrows* as the ledger
  grows unless the carried aggregate keeps the common query off the fold.

  This mattered because speed is one of the stated arguments for owning the tracker, and a
  175× claim justifies a release where a 1.9× claim does not. The arguments that do carry it
  are untouched: ownership of the harness's own state, the licence rider restricting a class
  of users, and twelve paid-for defects carried as requirements. The correction removes a bad
  reason for a good decision. The same figure was stale in `architecture.md`, which the bead
  had not named and which outlives the design document (`basicly-rxc1`).

- **A lane killed by `[runner] runner_timeout` no longer loses its work.** The kill
  takes the agent out before its last step, which is the commit — so the harness now
  commits whatever the worktree holds and lets the landing judge that diff, because a
  timeout is the harness's own decision and is not evidence against the change. The
  three killed runs on this repo's ledger discarded 47.8M tokens of it, nine tenths of
  everything ever paid for work that did not land, one of them a finished change that
  passed every check when it was committed by hand. Judged, never trusted: a red gate
  reworks the lane with real findings where an uncommitted tree could only produce
  "not-ready" and a second full dispatch, and the stall decision item is still queued
  either way, so a timeout stays visible to a human (`basicly-yvx9`).

## v0.7.1 - 2026-08-06

Delta: v0.7.0..v0.7.1

### Added

- **A new shipped skill, `interface-facts`, makes a third-party fact something you fetch
  rather than recall.** Before writing code, a design note, or any claim that depends on how
  an external interface behaves — a CLI flag, an API field, a model id, a price, a limit, a
  version — the skill has you establish it against the vendor's current documentation, and it
  applies the same standard to a claim a repo document already asserts.

  It exists because a recalled interface fact reads exactly like a verified one. A design
  document stated that one supported agent CLI reported no token counts, four hundred lines
  above its own section documenting the extraction mechanism and the runner code that
  implements it; the stale summary was read in preference to the section it cited and repeated
  to the owner as fact. Nothing in the harness could have caught that, because every gate the
  repo runs checks structure or behaviour — not whether a sentence about somebody else's tool
  is still true (`basicly-x8r1`).

### Changed

- **A lane records its changelog entry in its own file, so two lanes can never
  collide on one anchor.** A lane writes `changelog.d/<bead-id>.<category>.md`
  instead of editing `CHANGELOG.md`. The filename carries the bead id, so it is
  unique by construction and the collision becomes *impossible* rather than
  detected — the shape that blocked three of the four unattended-run attempts on
  2026-08-05/06 was two lanes at one anchor in a file no bead declared, each attempt
  in a different file, so enumerating them could never finish.

  `basicly release` assembles the fragments into the dated section, grouped under
  their Keep a Changelog heading and ordered by category then filename, and deletes
  them in the release commit. A hand-curated `## [Unreleased]` body still publishes
  alongside them, and a fragment whose category the operator already opened is
  appended to that section rather than opening a duplicate heading. An empty
  fragment, an unparseable filename, or a changelog with no `[Unreleased]` heading
  refuses the release before anything is written, because a lane's release note is
  never allowed to vanish quietly.

  `CHANGELOG.md` therefore leaves `[worktree] append_only_paths`, which is the point
  rather than a regression: that list bought detection by serializing every lane
  that touched the path, and there is now nothing to serialize (`basicly-4746`).

- **The `harness-loop` skill now opens with the tracker-write habit that a measured data loss
  earned.** Run `br sync --status` before any `br` write on a checkout you did not just leave,
  and `br sync --import-only` first if it reports the committed export is newer. A mutating
  command on a checkout in that state auto-flushes the *older database over the newer file*:
  measured once at a 426-record database published over a 612-record export, deleting 187
  records, 47 of them open, while `br create` reported success and no gate fired.

  Two details are what make it a habit rather than a note. The export is recoverable only
  because it is committed, so git is the backstop and the database is the side that gets
  corrupted. And the status line is computed from *timestamps*, so it also says the export is
  newer on a healthy checkout where the content is byte-identical and the import is a no-op —
  which is exactly how people learn to ignore it. The skill also asks you to check the shape of
  a tracker diff before committing: filing two beads is `+2` lines, so large deletions mean this
  is in progress.

  **This is guidance and a regression test, not a fix.** The underlying defect is still open
  (`basicly-b2n2`); what shipped is the habit that avoids it and a gate holding the requirement
  that a publish which would shrink the export must be refused rather than silent, so the
  eventual fix cannot land without satisfying it.

- **Two shipped skills absorbed process traps that had to be re-learned to be believed.** Both
  cost a real session, and both are the kind of thing a rule cannot convey by warning about it
  in general terms — so each is now a named section with the commands that work.

  The `harness-loop` skill gained a *Watching a lane* section. A dispatched lane is a
  subprocess of the engine rather than a subagent of the driving session, so nothing in an
  agent's own tooling lists it; the section carries the four read commands that do answer it,
  the rule that a worktree name replaces dots with hyphens (so watching the dotted path reports
  "no worktree" forever), and both ways process-polling fails. `pgrep -f <pattern>` and
  `pkill -f <pattern>` match the *caller's own* command line: the kill signals the invoking
  shell and the target survives, and an `until ! pgrep -f …` wait can never exit — it spins to
  timeout and reports the job as still running long after it succeeded, which is the damaging
  half.

  The `session-finish` skill now requires a durable artifact to be written straight to its final
  path, and forbids a background process outliving the session. The scratchpad is cleaned
  mid-session, which cost an 846-line design document and the transcript that produced it
  (`basicly-yjwu`).

### Fixed

- **A lane bounced by a landing conflict is re-dispatched with the conflict as its
  task, and an identical repeat escalates without spending the rework cap.** A
  bounced lane was already re-dispatched, but the bounce published nothing, so the
  supervisor assembled the prompt the agent had already satisfied — for work already
  committed on its branch. The agent changed nothing, the landing re-derived the
  identical conflict, and the second attempt escalated having learned nothing. This
  fired three times on 2026-08-05/06, and every one of those conflicts was resolvable
  by hand in about two minutes.

  The bounce now publishes a `coupling` found-info record naming the conflicting
  paths, which lanes landed over them, and both sides — the same channel the
  supervisor already used for a collision it *predicted*, which the collision it
  *observed* never got. It is written once the pass is over, not at the bounce, so no
  pass ordering reaches a durable record. There is still no merge-time resolution: the
  lane's own agent resolves on its own branch.

  A landing that then fails with the same cause on the same paths as that lane's
  previous one escalates to the decision queue immediately, and the attempt the loop
  charged for it is refunded — re-applying one branch to one anchor cannot converge,
  so the attempt could not have changed the outcome (`basicly-bdd4`).

- **A rework loop that is not converging now stops on the finding set, not on the
  attempt count.** Every gate that reports findings — a verify run's failed checks, a
  lane's failed rubric checks, a landing's own report — records them on the bead as a
  canonical member list, and the next round is compared against the previous one
  rather than merely counted. The count was never the measure: an attempt that
  re-derives the previous attempt's verdict verbatim was charged in full, and at
  `[policy] max_rework = 2` a node could reach a human having spent its whole budget
  re-reporting a finding set already on its own bead.

  One repeated round **warns**, on the bead and in the escalation the cap raises, so
  whoever triages it can see that a re-dispatch would learn nothing — a gate only
  reports what it checks, so one repeat may still hide a real change. Two consecutive
  repeated rounds **escalate immediately**, and the attempt is refunded so the
  remaining cap survives for whatever the human answers. A finding set that *grew*
  escalates on its first occurrence: the previous findings are all still open and new
  ones joined them. The refund is spendable once per bead and gate, so a node nobody
  answers still reaches its cap instead of being forgiven forever.

  The signature history and the comparison now live in one place next to the rework
  counter that owns this accounting, and the merge gate's repeat-bounce check
  (`basicly-bdd4`) delegates to it. Only the threshold stays per gate: a repeated
  landing conflict escalates on the first repeat, because re-applying one branch to
  one anchor provably cannot converge (`basicly-m4zv.5`).

## v0.7.0 - 2026-08-06

Delta: v0.6.0..v0.7.0

### Added

- **`basicly loop preflight <root>` answers the whole pre-run checklist as a command.**
  Read-only — it dispatches nothing, provisions nothing and writes no tracker state — and
  it exits non-zero when a run would be blocked, so CI or a wrapper can gate on it. One
  invocation reports the clean base (a dirty one refuses the landing *after* the lanes
  have already cost money), live worktrees, stale bindings that will be repaired, the
  resolved runner and its timeout, the grant and what remains under it, whether a metered
  runner has no budget covering it, how many lanes are dispatchable versus merely
  seedable, the measured per-lane cost, and either the live pass bound or a forecast for a
  full fan-out.

  Every one of those was previously an operator's recollection. A consumer installs the
  engine and inherits none of it, which makes such knowledge an undocumented runtime
  dependency — so the deterministic parts belong in a command, not a note (`basicly-ze8z`,
  which carries the audit classifying what the repo already enforces, what it does not,
  and what was never about running basicly).

- **A declared model tier now resolves to a concrete model at dispatch, or the
  dispatch refuses.** The seam that makes the tier vocabulary and the committed
  map do something: `basicly.models` reads the map and resolves
  (tier, vendor, surface) to the one id that surface accepts, and the runner pins
  it. Resolution is most-specific-first — an explicit `model` on
  `[[runner.agents]]`, then that agent's `tier`, then `[runner] default_tier`. An
  explicit id still wins, because naming one when a tier exists is a deliberate
  override.

  **An unresolvable tier refuses before anything is spawned**, naming the agent
  and the config key, so a dispatch never quietly runs on some other tier's
  model — the silent demotion the map's keyless `unavailable` cells exist to
  prevent. Nothing reads the network on this path; the map is committed data.

  The run record now carries the provenance rather than just the id: the tier,
  which input decided it, whether it was honoured, the models the adapter reported
  it **actually** used, and any mismatch between the two. Measured per family
  rather than assumed — claude reports its model three ways and keys `modelUsage`
  by the *dated* build while carrying the short `canonicalModel`; copilot reports
  it as its session store's `modelMetrics` keys, and one dispatch can name more
  than one; codex 0.146.0 reports no model anywhere, so codex is recorded as
  **unobserved** instead of assumed to match. Comparison tolerates a surface
  spelling and a dated build, so a healthy run is never flagged while a genuinely
  different model still is. A tier aimed at a family that cannot pin one at all
  (the handoff runner) is recorded as *not honoured* rather than as satisfied
  (`basicly-kjc5.59`).

- **A committed model map resolves a tier per vendor and per surface.**
  `.basicly/core/models/anchors.yaml` names one anchor model per (tier, vendor)
  and `.scripts/generate_model_map.py` resolves it against models.dev into
  `.basicly/core/models/model-map.json`: 4 tiers x 4 vendors (Anthropic, OpenAI,
  Moonshot AI, Google) x each surface serving them, with that surface's own
  published input/output cost and token limits. This is what makes the declared
  `tier` resolvable without pinning a provider id anywhere.

  All three axes matter. The same model is `claude-haiku-4-5` to Anthropic and
  `claude-haiku-4.5` to Copilot; **cost differs by surface too** — `gpt-5.6-luna`
  is 0.2/1.2 USD per MTok direct from OpenAI and 1/6 through Copilot — so a single
  per-vendor price would be wrong. And a tier can legitimately have **no** model
  on a surface: Copilot serves exactly one Moonshot model, so five of the 32 cells
  are `status: unavailable` with a reason and deliberately **no** `model` key. A
  consumer reading it fails loudly instead of silently getting a different tier's
  model. Vendors with a three-class ladder declare an explicit `collapse` of
  `maximum` onto `high`, cross-checked against the ids, rather than repeating a
  row silently. Anchors must clear a stated general-model rule (text in, text-only
  out, tool calling), so an image, TTS or embedding model can never become a tier.

  `--check` fetches and reports drift, naming the id and the change, and never
  writes: models.dev is community-contributed, so a bad upstream edit must
  surface as a red check rather than silently change which model runs your code.
  It is deliberately not a `[[verify.checks]]` entry — it needs the network, and
  a gate that needs the network must not run on every commit. The fetch happens
  at authoring and check time only, never in the dispatch path, so nothing gains
  a runtime network dependency.

  `model-map.json` is a standalone, self-describing artifact: plain JSON with a
  `schema_version`, a published schema beside it, a provenance stamp, and no
  basicly-internal structure. Copy that one file into an unrelated project and
  drive your own spawner from it — see `.basicly/core/models/README.md`
  (`basicly-kjc5.61`).

- **Subagents now project to the GitHub Copilot agents root as well as Claude's.**
  `basicly agents-build` — and therefore `basicly install` — writes each catalog
  subagent to `.claude/agents/<slug>.md` **and** to `.github/agents/<slug>.agent.md`.
  A consumer repo gains a projected directory it did not have before; commit it,
  like every other projection. `basicly agents-check` covers both roots with no
  opt-in flag, deliberately unlike `skills-build`, which still needs
  `--all-default-roots`.

  This reopens a decision `basicly-ajq` closed, on new facts rather than a fresh
  reading of the old ones. VS Code does read the Claude format out of
  `.claude/agents`, which is why one root sufficed — but it is not the only Copilot
  surface, and the others read only the documented root. The double-load objection
  is retired by measurement: Copilot deduplicates by the config file name minus
  `.md`/`.agent.md`, so `<slug>.md` and `<slug>.agent.md` collapse to one agent. A
  probe with `.claude/agents` moved aside confirmed the documented root alone
  carries the whole roster, so nothing rests on undocumented discovery. The tool
  alias table is pinned as reviewed data and the write-tool set is **derived** from
  it, so adding a write alias widens the read-only posture check automatically. The
  codex decline stands (`basicly-8sxf`).

- **A portable tier resolver ships into consumer repos**, at
  `.basicly/core/kit/tier_resolver.py`. It answers the same question
  `basicly.models` answers inside the harness — which concrete model does this tier
  mean on this host — under one hard constraint: **no basicly**. No `import
  basicly`, nothing on `PATH`, no third-party package, no network, no subprocess, no
  LLM. Two files are the entire dependency set, the module and `model-map.json`, so
  they can be copied into an unrelated project to drive its own spawner. Proved with
  `env -i`, `python -S -I` and an empty `PATH`.

  It is importable as a library and runnable as a CLI, and it resolves a tier
  declared by a **consumer's own** agent definition, not only by a basicly catalog
  source — that is what makes the tier vocabulary portable rather than an internal
  id. The one deliberate difference from the in-harness resolver: this one **fails
  closed and quiet** where that one raises. It runs in the spawn path, and on the
  Copilot host the hook can only be installed per machine, so it is invoked in
  repositories that have no map at all; returning an empty result leaves the spawn
  untouched and the host's own default applies. Empty is never silent — every empty
  result carries the reason it came back empty, and the CLI exits non-zero and
  prints it as JSON. Its mirrored surface table is cross-checked against
  `models.model_for` over all 4x4x3 cells (`basicly-wbsz.1`).

- **A Claude Code subagent now spawns on the tier its definition declares**, via
  `.basicly/core/kit/claude_tier_hook.py` — the injection half of the portable kit,
  wired in as a `PreToolUse` hook matching the `Agent` tool and carrying the same
  no-basicly constraint as the resolver beside it.

  **It writes an alias, not a model id**, because the two are different surfaces of
  the same host: the Agent tool's `model` parameter is a four-value enum
  (`sonnet | opus | haiku | fable`) that rejects `claude-opus-5`, while the
  definition *frontmatter* documents a full id as legal. `HOST_MODEL_ALIASES` on the
  kit holds the one tier→alias table, so the installer and any later host hook reuse
  it rather than each owning a copy, and a test holds that table to the map through
  `models.same_model` — the repo's own rule for whether a bare alias names an id —
  so it cannot drift into pinning a tier to the wrong class of model. An alias is
  never set without a model, so a cell the map marks `unavailable` pins nothing
  rather than naming what the map denies.

  **A repository with no map of its own is left completely alone.** The resolver's
  kit-adjacent fallback is deliberately switched off here (`beside_the_kit=False`):
  the kit is by definition always beside itself, so with the fallback on, a hook
  installed at user level — which is how it applies to every repo on a machine —
  would inject a model into unrelated projects. The hook also stands down when
  `CLAUDE_CODE_SUBAGENT_MODEL` is set (it outranks the parameter the hook writes, so
  a rewrite would be inert), when the spawn or the definition already names a model,
  and when nothing resolves. `updatedInput` *replaces* the tool input rather than
  merging into it, so the whole original input is carried through.

  Exercised the way a consumer would: a `basicly install` into a fresh scratch repo
  materialized both kit files, and that installed hook — run under `env -i`,
  `python -S -I` and no `PATH` — injected `opus` for a consumer-authored agent
  declaring `tier: high`, declined for a shipped agent that declares no tier, and
  declined from a directory with no map while reachable by absolute path
  (`basicly-wbsz.2`).

- **The tier injection kit installs itself**, from `.basicly/core/kit/install_hook.py`
  and the new `tier-injection` skill — the deliberate opt-in the kit needed, still
  with no basicly import, nothing on `PATH` and no third-party package. Default
  scope is the repository's own `.claude/settings.json`; `--user` is the explicit
  opt-in to every repository on the machine, and `--dry-run` prints what it would
  write. The user-level path reads `CLAUDE_CONFIG_DIR` rather than guessing a
  location per platform.

  **It is asymmetric by host and says so.** Claude Code gets a `PreToolUse` hook on
  the `Agent` tool. Copilot gets **nothing**, plus the reason: on CLI 1.0.77 there is
  no hook surface that fires for a spawn — no `hooks` directory under `~/.copilot`,
  no hook key in its settings, no hook option in `--help`, and a repo-level
  `.github/hooks` hook never fired across three earlier probes. Reporting success
  for a hook that can never fire would be worse than declining, so a run that
  installed nothing exits non-zero.

  Re-running converges rather than appending, matching an existing entry by the
  script it runs, so a moved interpreter replaces its own stale entry instead of
  racing it. Hooks the consumer wrote are untouched and unrelated settings keys
  survive. A `settings.json` that exists but cannot be parsed is **refused, never
  overwritten** — it is the consumer's file.

  **A run that writes now ends by saying the host CLI process must be quit and
  relaunched**, because hooks are read once at startup and clearing the conversation
  reloads neither them nor agent definitions — so the success line was where a
  consumer stopped, with every diagnostic they could reach reporting the hook
  correctly installed. A dry run and an already-installed converge run stay silent
  about it: nothing changed for a restart to pick up (`basicly-e3z6`).

- **The tier injection kit is documented**, in `.basicly/core/kit/README.md` (how to
  use it) and `docs/requirements/tier-injection-kit.md` (why it is shaped this way). They
  state which host resolves a tier dynamically and which falls back to static
  frontmatter plus `copilot --model`, name the four Claude hook traps the rewrite
  depends on — `updatedInput` replaces rather than merges, `model` is absent unless
  the caller set it, the `Agent` tool's `model` is a four-value alias enum rather
  than an id, and `CLAUDE_CODE_SUBAGENT_MODEL` outranks the injection — and show a
  consumer driving the map from another harness with the kit's four files under
  `env -i`. Every command shown was run against the shipped code.

  They also document the trap a new consumer hits first: **installing the hook does
  nothing until the host CLI process is quit and relaunched.** Clearing the
  conversation reloads neither hooks nor agent definitions, so the hook appears
  inert while every diagnostic reports it correctly installed (`basicly-wbsz.4`).

- **The kit's injection is now proven end to end, live, against a negative control.**
  Earlier verification drove the installed hook's emitted envelope; this closes the
  remaining gap — that the host honours it. With the hook installed and the process
  relaunched, a probe declaring `tier: low` spawned on `claude-haiku-4-5-20251001`
  from a `claude-opus-5` host, while a byte-identical probe with only the `tier` key
  removed spawned on the host default. Both models were read off the subagent
  transcripts rather than off the agents' own claims. The control is the point: a
  one-sided proof passes by pinning everything (`basicly-wbsz.3`, `basicly-wbsz`).

  Exercised against a real `basicly install` whose `.claude/settings.json` already
  carried basicly's own managed hooks and a 25-pattern deny list: all of them
  survived the merge, the second run reported `already installed` and changed
  nothing, and the exact command string the installer wrote — run verbatim under
  `env -i` — injected `haiku` for a `tier: low` agent and stayed silent for a
  shipped agent that declares none (`basicly-wbsz.3`).

- **A landed commit carries the model that produced it**, as a `Harness-Model` git
  trailer on the engine-assembled envelope, so model provenance survives a clone
  rather than living only in a local run record. The same trailer name the merge
  path already stamps, so `git log --format='%(trailers)'` reads the same fact off a
  work commit and a landing commit. It stamps the **pinned** value, since one
  trailer cannot carry the several models a session may switch between, and it is
  filtered to work phases so a decider dispatch cannot stamp the agent's commit.
  Nothing demanded, no trailer; a tier demanded but unanswerable **refuses** the
  envelope rather than emitting an empty or placeholder one (`basicly-kjc5.60`).

- **`basicly decompose` now forecasts spend and wall clock per model**, not only the
  working set. The governor only ever forecast working set — the context a lane
  needs — and measured spend on the three metered lanes ran **160–420x** that,
  because an agentic loop re-sends its context every turn. The forecast is three
  separately-replaceable ratios (tokens per working-set token, USD per million
  tokens, seconds per million tokens), seeded from a declared prior derived from
  those three packages and replaced by measured per-model history once
  `calibration_min_samples` is reached.

  **A seeded number is labelled seeded** on the surface a human reads, next to the
  number itself and not only in the recorded marker: a seeded figure that reads as
  measured is worse than no figure. An unpredictable metric prints as `unknown`
  rather than as a confident zero (`basicly-jr0l.21`).

- **`basicly release` refuses to tag while a shipped capability has zero recorded
  executions.** Exercised-or-unproven: the capabilities the repo *declares* it ships are
  derived from its own `[[verify.checks]]` and each is looked up in the ledgers already on
  disk — the `tool-usage` counters and the committed tracker-surface ledger. One with no
  execution refuses the release naming it, alongside every other pre-flight refusal and
  before the first byte is written.

  It is the deterministic form of the rule that a capability claim on a consumer-facing
  surface must be exercised before it is published: a false claim in code is caught by a
  gate, one in a README is caught by a consumer. The gate **fails closed** — declared
  capabilities with no ledger at all are unproven, not exercised, because reading a
  git-ignored file's absence as a pass is how a gate ends up green while doing nothing.
  The inventory is derived rather than curated for the same reason: a hand-listed one can
  be curated down to nothing and then passes forever (`basicly-irrm`).

- **`vulture` runs as a declared verify check, and a merge is gated on a reference from
  outside the module.** The wired-or-deleted gate fails when a symbol, config key,
  command or record field is referenced only inside its own module or under `tests/`.
  `vulture` was declared at `pyproject.toml:37` and called from nowhere — not by a
  check, not by a script — so the dependency that finds instruments nobody connected
  was itself an instrument nobody connected, and it is this gate's own first finding.
  Scoped to `src` and `.scripts`, and the omission of `tests` is the point: a symbol
  used only by its own tests is exactly what the gate is looking for. Suppressions live
  in `[tool.vulture] ignore_names`, vulture's only mechanism, and the gate fails on any
  entry that stops reproducing, so a suppression cannot outlive the finding it silenced.
  Confidence stays at vulture's default 60 rather than being raised, because the tiers
  above it report only unreachable code (`basicly-uexy`).

- **The tier ladder and the unit of cost ship as guidance, not just as a design
  document.** A new path-scoped `model-tier-routing` fragment loads when an agent edits
  `.basicly/core/agents/**`, `.basicly/core/models/**` or `basicly.toml` — the three
  places a tier is actually chosen. It states the four-tier ladder (`low`, `medium`,
  `high`, `maximum`) with the per-vendor classes behind it, and the rule that cost is
  measured **per landed correct change** rather than per dispatch, so a tier is picked
  for the reliability a role needs instead of for its sticker price.

  Until now nothing the harness distributed said either thing, so every agent choosing a
  model — and the roster design itself — reasoned from the price of one dispatch, while
  the weaker model's mistakes came back as rework, review cycles and bounced merges
  billed to the same change. Scoped rather than always-on because the always-on baseline
  is at its calibrated cap; it costs the claude and copilot baselines nothing and was
  written to the codex headroom that was left (`basicly-5xcj`).

### Changed

- **A run record carries the context the lane actually consumed.** `RunRecord` has
  carried `scope_tokens` and `forecast_tokens` since `basicly-jr0l.34` and has never
  carried the actual beside them, so every working-set number this engine gates on has
  been a proxy checkable only against its own output — which is how `working_set_max`
  came to be derived twice from a formula validated against itself. `record_dispatch`
  now writes `context_tokens` from `runner.context_occupancy`, the same final-turn
  occupancy the supervisor's context ceiling already meters, null wherever the adapter
  cannot report one (a chars/4 guess from stdout length would be worse than nothing —
  a calibration cannot tell an invented actual from a measured one).

  It matters more than the estimator change shipped beside it. Measured across those
  same 24 lanes, a lane's real context occupancy correlates with its declared scope at
  **R² = 0.095** — against 0.863 for turn count — and six lanes declaring no scope at
  all still occupied 106k–209k tokens. The term the formula is missing is a large
  ambient one, not a better read model, and no ambient constant is invented here on
  purpose: fitting a factor before the measurement existed is exactly how
  `basicly-z2wi`'s 216× happened (`basicly-fcls`).

- **A dispatch says which model ran, and a running lane reports itself.** The dispatch
  line named the adapter (`via claude`) and nothing about the model, so a run that
  resolved to a cheaper or dearer model than its declared tier read exactly like a correct
  one — and tier resolution is the entire point of the model map. It now carries the
  requested tier, which input decided it, the resolved id, the models actually observed
  when they disagree with the pin, and an explicit flag when the tier was **not** honoured
  (`basicly-e5a6`).

  Separately, a lane emitted nothing between adoption and completion: a healthy 519.6s run
  was indistinguishable from a wedge, and `pgrep` was the only way to tell. Each in-flight
  lane now reports its elapsed time on the heartbeat that already ticks during dispatch,
  stamped inside the worker so a lane queued behind the concurrency cap is not credited
  with run time, and measured on a monotonic clock. Tokens-so-far is `basicly-wctc`: the
  runner drains its pipes only after the process is down, so there is nothing incremental
  to read without restructuring the kill and timeout paths (`basicly-vu6u`).

- **`--runner` and `--autonomy` work on every loop command that can dispatch**, not only
  `supervise`. One committed `[runner] default` had to serve two incompatible modes — a
  real agent so a supervised pass dispatches at all, and the handoff so an interactive
  build does not re-implement the node in a second process — and the only escape was an
  uncommitted `basicly.local.toml` that no consumer inherits. `--runner manual` now
  restores the handoff for one invocation, and an unknown name is refused rather than
  silently read as the default (`basicly-nvm1`).

- **The scaffolded and built-in `[worktree] concurrency` default is 5**, up from 4, so a
  consumer inherits the parallelism this repo runs. Five also matches the default agent
  process budget of 8, which splits into exactly 5 lane slots plus the reserved decider and
  helper slots — the worktree cap and the process budget now agree instead of one silently
  throttling the other (`basicly-nvm1`).

- **The committed runner default is a real agent, so a supervised run dispatches
  out of the box.** `basicly.toml` shipped `[runner] default = "manual"` and the
  working default lived in a gitignored `basicly.local.toml`, which meant the
  committed intent never took effect and no consumer inherited it. The default is
  now `auto` — claude, then codex, then copilot on `PATH` — keeping the choice
  agent-agnostic rather than pinning one vendor, and `[worktree] concurrency` rises
  from 4 to 5.

  `[runner] runner_timeout` drops from the 3600s default to **1800s**. That is a
  cost control, not a preference: while an unsizeable lane defeats both dispatch
  cost gates (`basicly-vz78`), a per-lane wall clock bound is the only ceiling a
  runaway lane actually meets. Measured on the first supervised lane under this
  config — 4079243 tokens and 3.66 USD in 519.6s against a 3000000-token grant
  ceiling, a 36% overrun the ceiling could not prevent, because dispatch admission
  is read once per pass before any runner starts (`basicly-euyt`).

- **BREAKING: an agent source declares a model `tier`, not a provider `model`.**
  `.basicly/core/agents/<slug>/agent.yaml` — and its `.basicly-local/agents`
  overlay — now takes `tier: low | medium | high | maximum`, the portable model
  tier from the roster design, and a `model:` key fails `basicly catalog lint`.
  No projected agent file carries a `model` frontmatter line any more, for any
  agent family.

  **Migration.** Replace `model: <id-or-alias>` with the tier that alias sat in:
  `haiku` → `low`, `sonnet` → `medium`, `opus` → `high`, `fable` → `maximum`.
  Then run `basicly agents-build` to drop the `model` line from the projected
  file. The lint failure names the source and spells the four tiers, so the fix
  needs no reading of our docs.

  A provider model id is never portable across agent families: models.dev spells
  the same model `claude-haiku-4.5` for Copilot and `claude-haiku-4-5` for
  Anthropic, and only Claude reads a `model` frontmatter key at all — so a
  pinned id landed verbatim in one family's file and was invisible to every
  other. Declaring the tier is what makes the resolution above possible without
  re-authoring every source (`basicly-kjc5.58`).

- **The model tier vocabulary is validated in the agents overlay, not only in
  core.** The `tier` enum reached `.basicly/core/agents/*/agent.yaml` through JSON
  Schema validation, but `.basicly-local/agents` was never schema-validated, so an
  overlay source declaring `tier: turbo` was accepted in silence while the same
  source in core was rejected. Both are now checked, and by the same enum.

  **Migration.** If an overlay agent source carries a tier outside
  `low | medium | high | maximum`, `basicly catalog lint` now fails where it
  previously passed. The failure names the source and spells the four tiers
  (`basicly-axqe`).

- **The read-only posture check matches write tools case-insensitively**, and
  `Create` is now in the set. Copilot's tool aliases are explicitly case
  insensitive and its `edit` primary grants `Edit`, `MultiEdit`, `Write` and
  `NotebookEdit` — so a source declaring `edit`, `write` or `notebookedit` in
  lowercase passed our read-only check and was then granted real filesystem writes.
  `Create` had no Claude spelling at all, so the set structurally could not catch
  Copilot's file-creating primary.

  **Migration.** An agent source that declares a read-only posture while naming a
  write tool in any casing now fails `basicly catalog lint` where it previously
  passed. Either drop the write tool or drop the read-only posture
  (`basicly-e9jc`).

- **The working-set band is enforced at dispatch, not only at decompose.** The
  sizing governor refused an out-of-band plan at decompose and nothing re-checked
  the band when a lane started, so the band bound only work that arrived through
  decompose — a supervised pass over pre-existing leaf beads dispatched whatever
  the scheduler ranked first, at any size. Measured on this repo's own ready set,
  the top-ranked lane estimated 70% over the ceiling a plan would have been refused
  for.

  The two ends of the band earn different severities, deliberately. **Above the
  ceiling the dispatch is refused** and a pending queue item holds the lane, because
  the run would overflow the window it was sized against and the remedy — split the
  package — is a decompose action no engine can take. **Below the floor it escalates
  and then proceeds**, because an under-size lane still delivers and blocking it
  would strand deliverable work over an economic inefficiency. A lane whose scope
  cannot be read at all is **admitted**: most open beads carry no `## Scope` section,
  so failing closed on a missing estimate would turn a sizing governor into a ban on
  hand-filed work (`basicly-jr0l.16`).

- **A supervisor pass is admitted on what it is about to spend, not only on what it
  has spent.** The D3 ceiling compared spend *already recorded* against the grant's
  budget, so a pass was admitted whenever the previous ones happened to fit: a
  5000000-token ceiling admitted a pass that then spent 46026602 and halted on the
  pass after the money was gone. With concurrent lanes one pass can spend an
  unbounded multiple of a budget nothing checked it against. A pass now sums the
  forecast spend of the lanes it is about to start and refuses when that will not
  fit the remainder.

  **No running agent is ever interrupted.** The check runs before anything spawns,
  in-flight lanes still land through the routing layer, and a refusal costs no
  prompt assembly — cost is bounded by sizing the work, never by killing a working
  agent. Two rules keep the sum honest: a lane the working-set band already refuses
  is not counted, because it will not dispatch and charging the pass for it would
  refuse over money nobody was going to spend; and a lane whose scope cannot be read is
  counted at a conservative measured bound and **named as an assumption**, never
  presented as a forecast (`basicly-jr0l.22`, corrected by `basicly-vz78` below — this
  gate originally admitted a pass it could not forecast at all, which made it inert for
  most of a real tracker).

- **Three verification rules were added to the shipped skills**, each traceable to a
  wrong statement that reached a human. `harness-loop` now says to re-measure a
  bead's third-party claims before building on them and to record the check on the
  bead — a bead passes the Definition-of-Ready gate on structure, not on facts.
  `test-discipline` now says a zero result needs a positive control, in a search and
  in an absence assertion alike. `tool-br` now says to read tracker semantics
  (grants, gate results, derived phase) through the engine and not by grepping the
  export, which stays correct only for whole-tracker counting (`basicly-hsrs`).

- **`basicly verify` records the run's verdict as an artifact.** `basicly-m4zv.13`
  shipped the mechanism — a phase may declare a required evidence artifact and the
  advance is refused unless the declared path exists and is non-empty — but no producer:
  verify streams every check straight to the terminal and captured only on the diagnostic
  re-run, so a *passing* run wrote nothing anywhere and declaring an artifact for the
  verify phase would have refused every advance. `run_verify` now writes
  `.basicly/usage/verify-run.json` with the mode, timestamp, aggregate verdict and each
  check's name, status, return code and detail. Written from the one entry point every
  run goes through, so the CLI, the loop's build→verify transition and the merge queue's
  per-worktree run all produce it with no separate wiring. Self-ignored, like the run
  records, because a landing refuses dirt outside `.beads/` and every run rewrites it
  (`basicly-m0s4`).

- **Two shipped skills gained guidance their own incidents earned.** The
  `worktree-isolation` skill now warns that a *relative* `core.hooksPath` silently skips
  every gate in a linked worktree — the failure is silent, which is what makes it worth a
  line (`basicly-l7zo`). The `python` skill now names `pyright` as part of the pre-commit
  gate and gives the structural-typing convention for test doubles, and drops a stale
  line citation (`basicly-sco6`).

### Fixed

- **A validation error no longer leaks a home directory into a pasted log.** A load-time
  `ValidationError` rendered its source as an absolute path while `catalog lint` reported
  its own violations repo-relative, so one lint run showed the same finding in two path
  styles and the absolute one carried a username into anything pasted into an issue or a
  CI log. Both now render through one `display_path` helper: repo-relative inside the
  root, absolute outside it, since a path spelled with `..` would mislead more than it
  clarifies (`basicly-ky5z`).

- **A generated artifact every lane rebuilds no longer bounces the last lane to land.**
  The second variety of the class below, and the one none of that fix's remedies fit.
  `.basicly/generated-manifest.json` is regenerated by every catalog edit and appears in
  no bead's `## Scope`, so a three-lane pass over the worktree-isolation skill, the
  fragments and the python skill — provably disjoint sources — landed two lanes and then
  escalated the third on `rebase ... hit conflicts in: .basicly/generated-manifest.json
  (rework 2/2)`. Serialising the lanes buys nothing (the conflict is not semantic — the
  file is a function of the tree), giving one lane the entry is meaningless (every lane's
  edit legitimately changes it), and a union merge would corrupt it outright (it is JSON,
  not a line-oriented log).

  Such artifacts are now declared in `[worktree] generated_paths` with the deterministic
  `regenerate_command` that rebuilds them. When a landing rebase stops and **every**
  unmerged path is on that list, the queue discards both sides, re-runs the command in
  the lane's worktree and continues — no bounce, no rework, and no coupling recorded,
  because there is none to learn. The bound is the point: one undeclared path in the set
  and the whole rebase is aborted and handed back to the lane untouched, so the queue's
  standing rule that a source conflict is never resolved here is intact. A path list with
  no command is refused rather than silently doing nothing, a glob is refused for the same
  reason it is in `append_only_paths`, and the resolution is named in the landing's own
  detail line — an auto-resolution nobody is told about is indistinguishable from a rebase
  that never conflicted. `loop preflight` reports a `regen:` line beside `contend:`, since
  the two are the same collision with opposite remedies and this one was otherwise
  discoverable only at the merge queue. Nothing can land a wrong artifact quietly: the
  `projection-*` checks fail on a stale projection, so a bad rebuild bounces on the gate
  it already had (`basicly-lyro`). The engine ships the mechanism here; this repo declares
  its own manifest one commit later, because a landing runs the base engine against the
  worktree's config and a lane that adds a key and uses it in one commit cannot land
  (`basicly-69az`).

- **A path every lane appends to and no bead declares no longer serialises a pass
  invisibly.** `CHANGELOG.md` is written by essentially every code lane — the landing
  convention expects an `[Unreleased]` entry — and it appears in no bead's `## Scope`,
  so it was invisible to `decompose`'s grouping and to `loop preflight`'s band table.
  A three-lane pass over provably disjoint scopes (`schema.py`, `config.py`,
  `usage.py`) preflighted as `VERDICT: ready`, then two lanes landed and the third
  rebased onto an anchor that had moved twice, hit conflicts and spent both its rework
  retries there. The existing `shared` declaration cannot help: it only ever
  reclassifies a path a child already declared, and no child declares this one.

  Such paths are now declared once in `[worktree] append_only_paths` and fed into the
  same grouping `shared` feeds, from the other side — each one serialises the children
  that would collide on it, so the sizer orders them instead of the merge queue
  discovering them. A child that genuinely does not collide declares the path in its
  own `scope` *and* under `shared` and stays parallel. `decompose --dry-run` names the
  configured path and says where it came from, and `loop preflight` reports a
  `contend:` line naming the path and the lanes that will each append to it — the one
  collision knowable before any lane starts. The check is inert (and says so) until a
  consumer lists a path; a glob is refused rather than ignored. Auto-resolving such a
  conflict is deliberately *not* offered: a union merge of two prose entries is
  consumer-facing release copy nobody reviewed (`basicly-o8p0`).

- **An unknown section or key in `basicly.toml` / `basicly.local.toml` now fails the
  load instead of being silently ignored.** A `concurrency` written under `[loop]`,
  whose real home is `[worktree]`, was dropped without a word: the only symptom was
  the committed default of 5 continuing to apply, which is indistinguishable from the
  override having worked at the value it was already at, and it cost a dispatch
  decision made against a forecast the operator believed they had bounded. The
  overlay is gitignored and machine-local, so a stale or misplaced key there diverges
  one machine's behaviour from committed intent with no diff to review.

  A strict allowlist over the whole config *surface* (`config.CONFIG_SCHEMA`), not a
  denylist and not a schema derived from `config.py`'s own loaders — two live
  entries, `[[verify.checks]]` and `[[privacy.denied]]`, are read by a hook rather
  than by the module. The refusal names the file, the containing section, what that
  section accepts, and which sections accept a name like it, so the reported case
  reports `unknown section 'loop' ... its 'concurrency' is accepted in [worktree]`.
  Every command surfaces it; `basicly loop preflight` reports it as a first-line
  verdict rather than dying of a traceback partway down its checklist.

  **Forward compatibility, decided and recorded:** the refusal is unconditional — no
  warn-then-error staging, no narrowing to near-misses — so a repo pinned to an older
  basicly whose config carries a key added since fails until it upgrades or removes
  the key. That is the honest answer rather than a regression: an engine that cannot
  honour a key and runs anyway is the defect above, one version apart. Staging was
  rejected as unendable (the engine ships from `main`) and unread (the incident
  already printed a visibly wrong number that was skimmed past); near-miss narrowing
  leaves a genuinely novel key silent. The cost is bounded by the message, which
  names the engine version and says upgrading is one of the two fixes
  (`basicly-1piy`).

- **`basicly usage report` no longer presents heredoc terminators and Python keywords as
  terminal tools.** The counter file accumulates across sessions and is deliberately never
  reset, so it still carries rows written by recorders that have since been fixed: on this
  repo's ledger the tools table led with `t` (123), `-` (121), `-d` (120), `def` (90),
  `assert` (83), `PYEOF` (33) and `EOF` (28). That pollution was disqualifying in both
  directions — it invented tools nobody ran, and a real command shredded into fragments was
  undercounted, so a genuinely unused tool could read as used. The table is the culling
  input for `session-finish`, which is exactly the audit where only the never-used side can
  be fabricated.

  A recorded head now reaches the table only if this checkout can resolve it to a command:
  on `PATH`, in a repo-local bin dir (`node_modules/.bin`, `.venv/bin`, `.venv/Scripts` —
  `markdownlint-cli2`'s 168 runs are only ever reached through `npx`), or among the commands
  the catalog's own shell fences teach, so a tool this machine has not installed stays a
  tool. Everything else is counted into a named **Unresolved heads** bucket with its count
  and last-used date rather than dropped: those dates are what say the misses are historical,
  and a silently discarded miss is how the next recorder regression would go unnoticed. On
  this repo's counters the split is 108 tools against 393 unresolved heads, none of the
  latter recorded after 2026-07-31. Classifying at read time keeps the recorder's job
  observing and the reader's judging; the counter file is not rewritten (`basicly-3ymj`).

- **A dispatch now records its forecast in the unit its actual is metered in, so the
  forecast/actual pair is a comparison rather than a unit conversion.** `record_dispatch`
  wrote `forecast_tokens` — a *working set*, the context a lane holds at once — while the
  same record's `tokens` is *whole-lane spend*, which an agentic loop re-sends its context
  to accumulate. Comparing them yielded 64x-793x (median 307x) across 27 paired write
  dispatches, and that number read as a forecast wrong by two orders of magnitude:
  `basicly-gczc` was dispatched under an 8,000,000-token L3 grant on a forecast of 66,780
  and spent 16,963,245, so the grant halted after the work was done and the ship checkpoint
  dropped to a human. Every step of the engine behaved correctly; the number was wrong.

  The right-unit number already existed and was already trusted — `decompose.forecast_spend`
  computes it and `supervise.admit_pass_spend` refuses a pass on it — it simply never
  reached the record. It does now, as `forecast_spend_tokens`, beside the working set rather
  than replacing it: each has an actual of its own (`context_tokens` and `tokens`) and the
  turn multiplier still has to be measured from the cross-unit ratio, so both halves stay.
  The assumed bound a lane with no readable scope is gated at moves to the same field, being
  a quantile of measured lane actuals: in the working-set slot it paired at ~1x and looked
  like a perfect forecast of the wrong quantity.

  `decompose.spend_accuracy` is the gate, and `basicly usage forecast` now reports it under
  the existing table: actual over forecast **in one unit**, per recorded write dispatch,
  which must stay inside one order of magnitude either way — under-forecasting spends money
  no grant admitted, over-forecasting refuses a pass that would have fitted. It binds on the
  history that already exists rather than only on records written from now on, by re-applying
  today's calibration to the working set an older dispatch recorded; on this repo's committed
  ledger the same 26 comparable dispatches come in at 0.19x-2.37x, median 0.94x. A record
  whose working-set forecast the band itself would refuse cannot be converted and is named
  rather than dropped — one exists, `basicly-tcmy.31`, carrying a factor of ~193 from the
  spend-derived calibration `basicly-z2wi` deleted (`basicly-tcmy.34`).

- **An answered question no longer holds every delegated ship in the session until its
  bead closes.** The L3 lights-out preconditions counted `needs-input` and rework-escalation
  markers by their presence alone, and only a *closed* bead discounted them — so one open
  bead carrying a question a human had already answered refused every later delegated ship.
  Measured on the 2026-08-02 `basicly-tcmy` pass: two merged, verified children could not
  ship under the grant until the answered sibling was closed, at which point both were
  delegated with no further human input. Answering is a resolution exactly as closing is, so
  both marker families now retire on it, read from the decision queue's existing answer
  marker — no new state. An unanswered question still refuses, and a fact that blocks again
  after a wrong answer re-opens under the next generation and counts as live again
  (`basicly-jr0l.65`).

- **The exercised-or-unproven release gate now reads the engine's own record of a check,
  not who typed the tool's name.** Its witness was the check's `command[0]` counted by the
  `tool-usage` hook — a count of what an agent typed at a shell — which made the gate
  *unsatisfiable* for a check nobody types (`vulture` exists only as a `[[verify.checks]]`
  entry, so no verify run could ever create its counter, and the gate refused to tag
  v0.7.0 over a check it had just watched pass) and *unfalsifiable* for a check behind a
  wrapper (`wired-or-deleted` runs as `uv run python ...`, and `uv` at 6,091 executions
  would look identical with the check deleted outright). `basicly verify` now records every
  check it runs and watches pass into `.basicly/usage/verify-checks.json`, and the gate
  keys each capability by the check's own name — unique per declaration, and earnable only
  by the engine running that declaration. A tag therefore needs each declared check
  exercised in a mode that declares it; a check the engine has never run still refuses, and
  the fail-closed stance on a missing ledger is unchanged (`basicly-3yi3`).

- **A registered subcommand with no handler now fails loudly at every command group,
  not just at the top level.** Six sibling dispatchers spelled `return handler(args) if
  handler else 0` and a seventh (`usage`) did the same in a different shape, so a
  registered name nobody wired up printed nothing and exited **0** — indistinguishable
  from a command that ran, which is how such a mistake survives its own smoke test and
  reaches a consumer. `basicly-tcmy.4` had fixed exactly one of the eight sites. All
  eight now route through one `_dispatch` helper that exits 2 naming the offending
  subcommand, so a group added later cannot inherit the defect by copying its neighbour.
  The regression test derives its site list **from the parser** and is parametrised over
  every site, with a positive control that fails if the derivation ever stops recursing —
  the previous test asserted `len(actions) == 1` against the root parser and so never
  reached a nested group, which is precisely why the seventh site went unnoticed
  (`basicly-8ry8`).

- **Concurrent lanes can no longer read the shared tracker export half-written.**
  `scrub_export` rewrote the export in place while every lane read it through
  `.beads/redirect`, and `export_records` skips a line it cannot parse rather than
  raising — so a torn read returned a *partial issue set with no error at all*. It now
  publishes through a pid-scoped temp file and a rename, waiting out a reader that still
  holds the destination (Windows refuses `os.replace` while it is open, which would
  otherwise have made this a Windows-only failure) and leaving the export whole rather
  than half-written when it cannot win. A `DATABASE_ERROR` from the tracker is now
  classified transient and backed off, and the supervisor charges such a loss to the
  tracker gate instead of the lane's bounded rework budget, so a lane that never ran is
  not parked for the store's contention. The gate runs four real reader processes against
  a live writer with no retry in the read path; reverting the atomic write turns it red,
  with a reader observing 1,669 of 3,000 records (`basicly-vkh0.10`).

- **A dispatch that never started an agent no longer halts the whole grant.** The
  fail-closed rule from `basicly-jr0l.35` is about an agent run nobody could meter: its
  chars/4 floor cannot see the prompt, the tools or the cache writes, so counting it as
  spend would let the ceiling pass on a number that is not the session's spend. A
  dispatch that dies in pre-flight is the other case — no process ran, so nothing is
  hiding under the floor and the engine's own captured error is the whole transcript.
  Records now carry an `unstarted` outcome, and `session_spend` counts one as an estimate
  but not as an unmeterable dispatch; a completed run whose usage the adapter could not
  parse still halts, unchanged. A pre-flight failure also leaves telemetry now, where
  before the pass kept no evidence the lane had been attempted at all (`basicly-jr0l.64`).

  **This does not close the 2026-08-02 incident it was filed for.** The `tokens: 182`
  record that halted that grant is `phase: decide` — the *decider* agent invoked on the
  escalation the failed lane enqueued, not the lane. That halt is fixed separately, below
  (`basicly-gczc`).

- **A delegated decision no longer halts the grant, because the decider is now metered
  for real.** `decisions.invoke_decider` wrote a run record and carried a comment saying
  it was "metered like every other dispatch" — but it never passed `capture_usage`, so the
  record held a chars/4 floor flagged `estimated`, and under `session_spend` an estimated
  agent run *is* an unmeterable one, which zeroes the grant's remaining budget. One
  delegated decision was enough to end a pass, whether or not a lane ever failed; on the
  live record set the single unmetered dispatch among 213 is exactly that
  `basicly-tcmy.11` `phase: decide` entry. `rubrics.evaluate`'s judged dispatch had the
  same defect and the same halt.

  Two paths still halt, and deliberately: a dispatch that timed out, and one whose
  envelope does not parse at all. Neither reported usage, so neither is measurable, and a
  grant that cannot be metered is the one thing autonomy may not assume — the halt is the
  correct answer there rather than a residue of this defect.

  Both now pass the flag, which was never a one-line change: the same flag that makes
  usage reportable also wraps the reply — claude in a result object, codex in a JSONL event
  stream — and `parse_verdict` takes first-`{` to last-`}`, so it would have parsed the
  *envelope*, found no `decision` key, and failed closed to an abstention on every
  delegated decision while the token numbers finally looked right. So `runner.result_text`
  undoes the envelope (claude's `result` field on either envelope, codex's last
  `agent_message`, copilot's stdout untouched — it measures out of band), and both call
  sites read the answer back through it. Every field was taken off a live probe of the argv
  the engine really dispatches, not from documentation.

  Measured on a real confined decider dispatch: 17,648 adapter-reported tokens and
  \$0.179 where the floor would have reported 1,297 — **13.6x under** — and against the
  real 213-dispatch record set the fixed dispatch leaves `unmetered_dispatches` unchanged
  and an L3 grant funded, where the pre-fix shape of the same dispatch halts it. Each call
  site also carries a test that fails if the flag and the prose beside it stop agreeing,
  which is the defect class that put a false metering claim in the comment to begin with
  (`basicly-gczc`).

- **Scope read-cost sizes the material a lane reads, not the whole of every file it
  names.** A scope of `src/basicly/cli.py` cost all 45,556 of its tokens, so a
  three-line change to it estimated 139,448 working-set tokens and the band *refused*
  it — while the harness's own always-on `tool-usage` guidance told the same agent to
  "read only the ranges you need". The estimator and the instructions described
  different agents, and the estimator held the gate: nothing touching `cli.py`,
  `supervise.py` or `architecture.md` could be decomposed at all.

  Re-measured over 185 (lane, file) pairs from 24 recorded headless lanes, taking the
  union of the line ranges each lane actually read: 78% of `Read` calls are ranged, a
  file under roughly 4,000 tokens is read whole, and above that the material taken out
  is *flat* at ~1,500 tokens however large the file gets. So the model is a per-file
  cap rather than a curve, and `decompose.SCOPE_FILE_READ_CAP` is 4,000 — the
  transition itself, which covers the material actually read in 86% of those pairs and
  over-states the large end by about 1.5×, on the standing rule that over-reading costs
  a false refusal a human can see while under-reading admits work the band should have
  refused. Capped per *file*, so a lane naming three large modules still outsizes one
  naming a single module. The glob **grammar** is untouched — eleven consumers read a
  scope glob as a set of paths and only the sizing chain reads it as a quantity — and
  one test per consumer now pins grouping, scope-overlap collision detection and merge
  coupling attribution as invariant to the file size the cap acts on.

  `working_set_max` follows the estimator down, 112,000 → 56,000 → **72,000**, by the
  same rule `basicly-3w44` derived it with. The third move is the instructive one: 56,000
  was derived from `basicly-tcmy.31` while the lane deriving it was still running, and the
  record that lane wrote on finishing — 72,000 — contradicted the constant it had just
  committed, so its own gate refused its own landing. Anything derived from the dispatch
  record is true only as of the last dispatch, and the derivation is a ratchet whose input
  is a lane's own declared scope (`basicly-qorx`). Both outcome populations are now sized
  by *one*
  function from *one* source: a recorded `scope_tokens` is denominated in whatever
  measure was current when that dispatch ran, so preferring it mixes two quantities
  into the one comparison the gate exists to make. That symmetry also retires a claim
  the ceiling rested on — `basicly-kjc5.42` and `basicly-kjc5.44` declare the identical
  class and the identical scope, and one completed while the other was SIGTERMed, so no
  function of (class, scope) can separate that pair and no ceiling can be credited with
  refusing the second. The previous derivation appeared to only because the
  completed-side query dropped kjc5.42's success on the same optional-field filter
  `basicly-ipx2` had just removed from the failure side (`basicly-fcls`).

- **The `docs-claims` gate runs on Windows.** It was wired as a bare
  `python .scripts/docs_claims.py`, on the reasoning that this matched the bare-binary
  convention of every other check. That convention holds for *console scripts* — `ruff`,
  `pyright`, `bandit`, `pytest`, `basicly` — which the venv installs into its
  `bin`/`Scripts` directory; it does not hold for the *interpreter*. On windows-latest a
  bare `python` resolves to a system interpreter with neither `yaml` nor `basicly`
  importable, so the script died at import time and this one check failed the Windows
  quality-gates job while passing on ubuntu and macos. Its `fix_command` carried the same
  defect, so a Windows contributor's stale block was never regenerated either. Both now
  run through `uv run python`, as every other repository script invocation already did,
  and so does the repair hint the failure prints.

  `tests/test_verify.py::test_no_verify_check_invokes_a_bare_python_interpreter` reads the
  invocation form out of the config rather than running it, so a future check added with a
  bare interpreter fails on every platform instead of only on the runner that would break
  — the fourth platform-only defect to reach main is what put the rule in a test
  (`basicly-tcmy.32`).

- **The permissions projection is gated like the other four.** `basicly` shipped
  `permissions-build` and `permissions-check`, and `install` ran the build — but
  `permissions-check` appeared in no `[[verify.checks]]` entry, no pre-commit hook and no
  CI workflow. Editing `.basicly/core/permissions/permissions.yaml` and committing
  therefore shipped an unbuilt agent deny-list while all four documented projection gates
  reported green: the fifth pair had the exact hole the other four were added to close.
  A `projection-permissions` check now runs in `fast` and `full`, so the drift fails at
  commit time and names the missing pattern and the file it is missing from.

  The always-on commands fragment (and `CONTRIBUTING.md`) list the fifth gate with the
  others, and `tests/test_verify.py` no longer hand-maintains the set it asserts: it
  derives the required subcommands from the CLI's own handler registry, for both the
  verify wiring and the documented list. A sixth pair cannot be omitted from either the
  same way (`basicly-tcmy.23`).

- **The repo's only architectural gate now describes modules that exist, and fails when
  one imports upward.** `.importlinter` declared a single `forbidden` contract naming
  `basicly.fragments` and `basicly.targets`. Neither module existed and neither
  structurally could — fragments and targets are YAML under `.basicly/core/`, never
  Python under `src/basicly/` — so `lint-imports` reported `1 kept, 0 broken` over 48
  files and 149 dependencies forever, in this repo and in every consumer repo, on both
  the `fast` and `full` verify paths. Nothing else enforced layering; the real ordering
  existed as convention plus two `# noqa: PLC0415` comments.

  It is replaced by two `layers` contracts: `engine-layering`, the engine's fourteen
  tiers from `cli` down to the dependency-free leaves, and `renderer-layering`, the
  per-target renderers above their shared helpers. Both set `exhaustive = True`, so a
  new module cannot join the package without being placed in a tier. Siblings are
  declared independent, so a tier is a tier and not a bucket.

  The two surviving cycles (`loop`/`supervise`, `policy`/`decisions`) are carried as
  `ignore_imports` entries for the deferred direction only. That is not a weakening:
  `unmatched_ignore_imports_alerting` defaults to `error`, so removing a cycle breaks
  the contract until its exemption is removed with it.

  `tests/test_import_contracts.py` is the control pair the old contract could never have
  passed — the same staged copy of the package checked unchanged and again with one
  violation injected, asserting both module names appear in the failure
  (`basicly-tcmy.2`).

- **`skills-check` now reports a hand-authored file under a projected skills root instead
  of exiting zero.** `check_synced_skills` iterated the *catalog sources* and compared each
  against its projection, so a directory no source named was never visited. The
  `release-process` skill lived that way: a tracked, hand-written
  `.claude/skills/release-process/SKILL.md` with no `skill.yaml`, therefore never projected
  to `.agents/skills/` — Codex could not see it — while `skills-check
  --all-default-roots`, `catalog lint` (it scans only `.basicly/core/`) and the generated
  manifest all passed. For a tool whose claim is one catalog projected under drift gates, a
  skill the projector did not know about was a hole in the product. The check now also
  scans each root for entries no source accounts for and names them; a deselected skill
  keeps its own `excluded by technology selection` reason. It **reports, never prunes** —
  `skills-build` mirrors only inside a directory it owns, and deleting a file no source
  describes would destroy the only copy — so the remedy line says so rather than advising a
  rebuild that cannot help. `release-process` now projects from
  `.basicly/core/skills/release-process/skill.yaml`, trimmed to `basicly release` plus the
  two steps it deliberately leaves to a human (decide the version, push); the hand-run
  workflow it contradicted — whose commit subject the repo's own `commit-msg` gate would
  have rejected, and which documented changelog sections `CHANGELOG.md` does not have — is
  gone, as is the stray `.claude/skills/README.md` that taught the same wrong model
  (`basicly-tcmy.8`).

- **A declared scope no longer counts the virtualenv, dependency trees or caches as the
  lane's working set.** `decompose._scope_files` globbed with no ignore list, so
  `SCOPE_EXCLUDED_DIRS` (`.git`, `.venv`, `venv`, `node_modules`, `__pycache__`, the tool
  caches) now drops those paths. Measured here: `**/*.py` matched **2229** files of which
  **2077 were the virtualenv** — 147 were source; after the fix it reads 147 files and
  710,316 tokens instead of 6,337,230. `tests/**` fell from 1,796,401 to 383,569 tokens,
  because `__pycache__` `.pyc` files were being read as text via `errors="replace"`.

  This was not merely a wrong number. The band refuses a lane over `working_set_max` and
  that refusal sets `human_required` on the queued escalation, so an inflated estimate held
  a lane pending a human; the same read-cost feeds `calibrated_build_factors`, so every
  calibration sample inherited it.

  Exclusion is by **directory name**, never by a leading dot — `.basicly`, `.claude`,
  `.github` and `.beads` are legitimate scope, and excluding them would silently zero their
  read-cost. `dist`, `build` and `site` are deliberately **not** excluded: basicly installs
  into consumer repositories where each can be a real source package, and a wrong exclusion
  under-reads a lane and admits work the band should have refused (`basicly-jr0l.63`).

- **Fan-out provisioning now picks the highest-ranked dispatchable children, and skips a
  lane the band refuses.** `_ensure_child_worktrees` computed `loop_state.ready_ranked` —
  documented as "ranked by `br scheduler` (highest priority first)" — and then reduced it
  to a membership set, iterating br's dependents order instead. Because provisioning is
  capped at `[worktree] concurrency`, that arbitrary order decided *which lanes were in
  the pass at all*: `supervise.ready_lanes` rank-orders dispatch, but it can only order
  the set provisioning already chose. So the ranking was computed and thrown away.

  Measured on `basicly-jr0l` before the fix: the five slots went to four children with no
  readable scope — each then counted at the 16,002,352-token unsizeable-lane assumption —
  plus one under the floor, while five in-band, DoR-ready children of the same root were
  never provisioned. A grant sized for the measured lanes would have funded the unmeasured
  ones.

  Provisioning also consulted no sizing at all, so an over-ceiling child took a worktree
  that dispatch then discarded — `escalate_working_set` leaves a pending decision and
  `ready_lanes` filters on it — spending a concurrency slot on a lane nothing runs in. An
  **unsizeable** child is still provisioned: an unreadable scope is not a refusal
  (`admit_working_set` sets `refused` on the ceiling alone), and dropping it here would
  lose work rather than defer it (`basicly-jr0l.62`).

- **The bound for a lane with no readable scope is a high quantile of measured lane
  actuals, not their median.** `[policy.sizing] unsized_lane_quantile` (default `0.9`)
  replaces the median, and the target is stated as an overrun rate: at most one lane in
  ten should exceed its bound.

  The median was chosen when the recorded population looked bimodal — leaves apparently
  856,182–4,079,243 tokens and lane packages 7,674,671–20,594,047 — so any high quantile
  would have priced a leaf like a package. **More data refuted that split**: four leaf
  lanes measured 9,418,977, 10,834,801, 11,478,450 and 11,867,602 tokens, inside the
  supposed package band. The population is one wide spread, so the median was not the
  centre of a tight cluster but the midpoint of an order-of-magnitude range — exceeded
  by 8 of 17 recorded actuals (47%). A four-lane pass forecast at 16,316,972 tokens
  spent 43,599,830 against a 21,000,000 grant (`basicly-jr0l.58`).

  **An unsizeable lane now also records the bound it was gated on as its forecast**
  (`forecast_source: assumed:<source>`, namespaced so an assumption cannot be read as an
  estimate off a declared scope). Without it the calibration telemetry was unobtainable
  from exactly the dispatches that needed it: after a completed four-lane run,
  `basicly usage forecast` still reported no dispatch carrying both halves.

- **A lane queued behind the concurrency cap no longer starts once the grant is
  exhausted.** Spend admission was a pass-entry verdict: `dispatch_lanes` read the
  ceiling once, before any runner started, and nothing re-checked while the pass ran.
  Lanes waiting for a slot were therefore cleared to run on a reading taken before any of
  them had spent anything. The ceiling is now re-read at the moment each lane actually
  starts, and a lane whose turn comes after the budget is gone does not start.

  Measured on the first multi-lane run: a pass admitted at a 16,316,972-token forecast
  ran to 43,599,830 against a 21,000,000 grant, and the halt printed only after the last
  lane had exited (`basicly-jr0l.59`).

  **This bounds the queued case only.** A lane already running is never interrupted —
  cost is bounded by sizing the work, not by killing a working agent — so lanes started
  concurrently inside one cap-sized batch are still bounded by their forecast alone.
  Closing that gap needs in-flight token accounting, which `runner.run` cannot yet
  provide because it drains its pipes only after the process exits (`basicly-wctc`), and
  a forecast that is not biased low (`basicly-jr0l.58`).

- **One shared path no longer collapses every child into a single serial group.**
  Grouping is the transitive closure of scope overlap, so several children that each
  declared a common `pyproject.toml`, lockfile or config manifest beside their own
  distinct module all overlapped each other through that one file — the closure merged
  them into one group and serialized work that was almost entirely parallel. The more
  honest the plan, the worse the grouping, because a careful author is *more* likely to
  declare the manifest they will touch.

  A child may now list part of its `scope` as `shared` — the paths it touches but does not
  own — and overlap through a path **both** sides declared shared no longer serializes
  them. One child *owning* the path still blocks everyone who touches it, so the
  declaration is only ever as strong as the weakest claim on the file. The hatch is
  deliberately narrow, because a plan is agent-authored and must not be able to hide a
  real collision: an entry must appear verbatim in `scope` (so the recorded `## Scope`
  stays the whole truth for sizing and merge-time attribution) and must be one literal
  path, never a glob, so no subtree can be exempted behind a wildcard.

  Declared or not, the collapse is no longer silent. `basicly decompose` (dry run and real
  run) and the loop's advance detail now **name the load-bearing path**: every declared
  glob whose removal would leave the plan in more parallel groups, with the count both
  ways, marked as still collapsing or as already defused by a `shared` declaration. A
  serial chain with no stated reason was most of the damage — the one-line fix was
  available all along and nothing said where to make it (`basicly-jr0l.45`).

- **A projected Claude hook resolves from any working directory.** The command was
  rendered with a **relative** script path, justified by mirroring the pre-commit entries.
  That precedent does not transfer: a pre-commit hook always runs from the repo root, while
  a Claude Code handler runs in the *current* directory — so every consumer's managed hook
  failed the moment the working directory drifted. Seen here as `tool-usage.py` failing
  from a subdirectory; the `PreToolUse` case is worse in kind, because `protect-generated`
  is a guard and a guard that cannot start protects nothing.

  Now `uv run --no-project --no-python-downloads python
  "${CLAUDE_PROJECT_DIR}/<path>"` — the host substitutes the placeholder as a plain string
  before any shell sees it, so it also holds under PowerShell, and no machine-specific
  absolute path lands in a tracked file. `basicly-dukb` had already established this from
  the vendor docs and the tier-injection kit already shipped it, so the repo simply was not
  eating its own dog food. Re-projection replaces the old form instead of duplicating it,
  because the managed-group matcher keys on the relpath-qualified script the new command
  still contains (`basicly-f3mi`).

- **An unsizeable lane no longer defeats both dispatch cost gates.** Both keyed on
  `decompose.dispatch_sizing`, which returns `None` for any bead with no `## Scope`
  heading — 56 of 83 open beads here — and both read that as "nothing to compare" and
  admitted. `PassSpendAdmission.refused` is `violation is not None`, so a pass of
  hand-filed lanes had **no forward bound at all**, and nothing printed the missing
  coverage. Measured: one lane spent **4079243 tokens against a 3000000-token ceiling**,
  a 36% overrun the ceiling could not prevent, because dispatch admission is read once
  per pass before any runner starts and no running lane is interrupted.

  Such a lane is now counted at `decompose.unsized_lane_tokens` — the median of recent
  measured lane actuals, falling back to a declared seed — and the coverage is reported
  on **every** pass, admitted ones included, because an unbounded pass previously looked
  identical to a checked one. The statistic is deliberately a central estimate, not a
  worst case: the lane population is bimodal (leaves 856182–4079243 tokens, lane
  packages driving sub-tasks 7674671–20594047) and nothing in a run record tells them
  apart, so a maximum would set every leaf's bound from a package and refuse passes that
  genuinely fit. It is one layer of three — `runner_timeout` bounds a lane hard, and the
  retrospective halt bounds the session — and it still refuses the case that failed
  (`basicly-vz78`).

- **`loop supervise` can start work.** It could not: `ready_lanes` returns only lanes at
  phase `build`, a bead reaches `build` only by acquiring a worktree binding, and the code
  that provisions one sits on no supervise path — so a cold root printed "no ready lanes
  and nothing to land" and exited while dozens of dependency-unblocked children sat at
  `intake`. Three handovers documented that command as the one that runs the factory. The
  pass now seeds by delegating to the root's own advance, so the decompose checkpoint, the
  worktree cap and the ready-set filter keep their single definition; a root that cannot
  seed reports why and stops rather than spinning (`basicly-t73d`).

- **A metered dispatch requires a token budget.** Both halves of the spend ceiling key on
  the grant — `spend_status` reports `halted=False` and `check_pass_spend` admits any
  forecast against a `None` remainder — so an ungranted session had no bound whatsoever.
  Latent while the supervisor could not seed itself; one command deep once it could. A
  headless dispatch now refuses without a covering budget and says how to issue one, while
  a handoff proceeds because it spends nothing. Checked **before** provisioning, so a
  doomed pass no longer pays for a `uv sync` and an `npm install` per lane
  (`basicly-kkux`).

- **A worktree binding that outlives its worktree no longer wedges its bead.**
  `derive_phase` reaches the `build` rung on the *binding*, which is tracker state, while
  the worktree is filesystem state — so a bead whose worktree vanished derived `build`
  forever, invisible to `ready_lanes` (non-live) and skipped by `advance_parked`: past
  classify and undispatchable at once. `derive_session` already flagged the case and its
  own comment said such a lane "needs a re-dispatch, not an adoption"; nothing acted on
  it. The supervisor now disposes of it, clearing the ref when the branch proves nothing
  is unlanded and escalating when commits could be orphaned (`basicly-1koh`).

- **The wait meter no longer fails the verify gate on clock granularity.**
  `_assert_interval` gave the upper bound slack for real `br` round-trip cost but left the
  lower bound bare, which asserted that the tracker's whole-second stamp can never land
  ahead of the local clock reading. Under four-worker load it did: `600 <= 599`. A flake
  inside `verify` is a factory defect rather than a test annoyance, because a flaky gate
  consumes a lane's rework budget as if the work were wrong (`basicly-5h0g`).

- **An autonomy grant now covers a track assembled from gating edges, and says how
  many beads it covers.** A grant's session was its root plus that root's
  parent-child descendants, so a grant issued on a root that *gates* its work rather
  than parenting it covered exactly one bead — its own. A release epic is exactly
  that shape: a bead's parent is its epic of origin and nothing is re-parented, so
  the release holds its track as `blocks` dependencies spanning several parents plus
  beads with no parent at all. The first checkpoint under an L3 grant on such a root
  still demanded a confirm code, and the grant's token ceiling metered nothing.

  The session walk now follows both edges — parent-child dependents for the
  decomposition, `blocks` dependencies for the cross-cutting track. The direction is
  asymmetric on purpose: work the root waits *on* is the track the grant was issued
  over, while work waiting *on* the root is downstream of it and stays outside. The
  widening applies to the whole session contract, so a gated bead's spend now counts
  against the budget and its needs-input and rework events now carry the "any
  wrinkle" weight L3 already claimed for them.

  Coverage is invisible from the grant marker itself — an L3 with a 25000000-token
  ceiling reads the same over twenty beads as over one — so issuance and the ledger
  read now both report the count, and a session of one names itself as such
  (`basicly-jr0l.40`).

- **The tier injection kit no longer writes a machine-specific command into a
  committed file.** At its default project scope the installer rendered both the
  interpreter and the hook script as absolute paths, so installing it wrote a home
  directory and a username into `.claude/settings.json` — a tracked, shared file —
  and produced an entry that was broken for every teammate and every other machine.
  The repository's file now gets a command with nothing machine-specific in it: the
  hook is named through `${CLAUDE_PROJECT_DIR}`, which the host substitutes itself
  and which therefore does not depend on the directory a spawn happened in, and it
  runs under `uv run --no-project --no-python-downloads` — no absolute path, network
  free, and identical on Windows, Linux and macOS. `--interpreter` overrides that for
  a consumer without uv. **`--user` scope is deliberately unchanged**: that file is
  machine-local, so absolute paths are correct there and nothing needs to be on
  `PATH`. A project-scope install that cannot name the hook relative to the
  repository now refuses rather than falling back to the absolute rendering.

  The reason the suite could not see this is fixed too. Every test installed into a
  bare `tmp_path` while running the installer out of basicly's own checkout, so the
  hook was never inside the repository being written to and no test could observe how
  a real consumer's committed file gets addressed; the tests now install into a
  repository that contains the kit. The assertion that had pinned the defect was
  justified by an unverified claim in its own docstring — that claim turned out to be
  true and simply never to have been an argument for an absolute path (`basicly-dukb`).

- **A closed bead's rework escalation no longer blocks lights-out forever.** Rework
  is recorded as append-only comment markers and nothing marks an escalation
  resolved, so once any bead in a session tree reached `max_rework` its count never
  decreased — a bead that shipped days earlier, with its checkpoint answered by a
  human, was still read as a live session-wide violation and every ship under that
  root demanded a confirm code despite an active grant. Closed beads are now
  excluded, for the escalation rule and the `needs-input` rule both, through one
  shared reader so the grant rule and the escalation rule stay one principle. An
  **open** bead's escalation still blocks, unchanged (`basicly-i1s8`).

- **A hook script change can pass the landing verify from a worktree.**
  `hooks-check` compared the installed package's hook directory against the repo's,
  and skipped the comparison when the two resolved to the same path — but a landing
  verify runs with the repo root set to the lane's worktree, so an editable install
  compared the pre-merge base copy against the post-change worktree copy and
  reported the change itself as stale projection. It is now compared as a
  projection. The remedy line was wrong too: it named `basicly hooks-build`, which
  deliberately does not copy hook scripts and cannot fix a script mismatch. The
  message now names the command that applies, and says that `basicly install`
  overwrites the local copy — so a deliberate hook-script edit is redirected to its
  catalog source instead of being destroyed by the fix (`basicly-9o6s`).

- **A confirm-code challenge names the precondition that declined it.** A grant that
  covered the checkpoint, was not spend-halted, and still declined for a specific
  reason produced a bare `CONFIRMATION REQUIRED` — indistinguishable from having no
  grant at all, which made a ship refused by a wrinkle in a **sibling** issue
  unreadable. The reason now prints first, because it is the only part an operator
  can act on. A session with no grant reads exactly as it always did
  (`basicly-5ltn`).

- **`basicly usage report` credits the real tool behind a wrapper.** Command
  resolution stopped at the wrapper, so `uv run --directory <worktree> pytest`
  credited the worktree's basename and never credited `pytest`, and `env -C <dir>
  <cmd>` credited `env`. Wrappers, their subcommands and their value-taking flags
  are now walked past to the actual command; inline code is not counted as a tool,
  and a shell function defined in the command text is not counted as one either.
  This matters because the report is what names never-used catalog skills as
  culling candidates, so noise in it can drive a real culling decision
  (`basicly-m0p1`).

- **A vanishing bytecode cache no longer races the hook-sync test** under
  `pytest -n 4`. CPython writes a `.pyc` as a uniquely named temp file and renames
  it, so a concurrent tree walk could stat a name that no longer existed. The test
  fixture was copying the catalog hooks directory raw while production already
  filtered the same walk, so the fix was to make the fixture filter too rather than
  to suppress bytecode writing. A flake in a gate costs more than its runtime: it
  burns the loop's bounded rework budget (`basicly-y1wk`).

- **A stale context-window constant was truncating healthy lanes, and the window is now
  declared per agent with a falsifier.** The engine declared `claude`'s window as
  `200_000` while the dispatched model serves `1_000_000`, so the finalize trigger sat
  near 120000 instead of ~600000 and lanes were cut short and spun into follow-up beads
  for months — twelve of them. The repo's own ledger had contradicted the constant the
  whole time: a recorded occupancy of 223221 cannot fit a 200000 window.

  The window now lives in `[runner.context_windows]` in `basicly.toml` rather than in
  engine code, because a repo whose runner pins no model must *declare* what it
  dispatches — the model is only knowable after the run, from what the CLI reports it
  ran. And the declaration is falsifiable: `runner.window_violations` reports every
  recorded occupancy above its agent's declared window, wired to a test that fails
  naming both figures the first time a lane records an occupancy the declaration says is
  impossible. Had it existed it would have caught the 200000 the first time a lane
  recorded 210721.

  **This class of constant may not be fixed by pasting in a fresher number** — that is
  the same unchecked declaration one generation on. It is the one kind of value that is
  correct when written and rots silently as the vendor ships, so no gate catches it and
  no review re-reads it. Where a field measures the same quantity a constant declares,
  wire the comparison as a check (`basicly-23ep`).

- **The context ceiling now applies on the single-track dispatch path, not only the
  supervised one.** The ceiling constants and the finalize protocol lived only in
  `supervise.py`, and the protocol had exactly one caller — so `basicly loop run`
  measured a lane's context occupancy and then did nothing with it. The two write paths
  could reach opposite conclusions about a bead's fate for reasons having nothing to do
  with the bead. Both now call one shared `meter_context_ceiling`, replacing the
  supervised path's inline copy, because a duplicated ceiling is how the two came to
  disagree in the first place (`basicly-7kxq`).

- **A `deferred` child is no longer sized, funded or dispatched.** Open children were
  defined as `status != "closed"`, and beads has at least three non-closed statuses — so
  deferring a bead removed it from nothing: it stayed in the candidate set, was sized
  into the band table, counted toward the open-child total, and was funded by the pass
  forecast. It also held its epic open indefinitely. Excluded now at both sites that ask
  the question (`basicly-toj6`).

- **`basicly loop preflight` refuses its verdict when no lane could be provisioned.** It
  reported `VERDICT: ready` for a pass that then dispatched nothing, which is the one
  situation the command exists to prevent — its whole job is to answer the pre-run
  checklist before a fan-out costs wall-clock and money. Three distinct causes reach that
  state: an epic whose children have all closed, an epic whose every child is refused by
  the band ceiling, and an unapproved `decompose` checkpoint that a covering grant cannot
  serve. The verdict now refuses and names which one:

  ```text
  provision: NONE - 8 child(ren), none open; nothing left to provision a lane from
  VERDICT:   not ready - the session has no open child to provision a lane from
  ```

  It also reports the root's own pending checkpoints, distinguishing one a live grant
  delegates from one it cannot serve — reporting every unapproved checkpoint as a blocker
  would make the verdict noise. Still read-only, and it still exits non-zero when a run
  would be blocked, so CI or a wrapper can gate on it (`basicly-cdhq`).

- **The security scan now covers the portable kit, and a directory can no longer arrive
  outside it unnoticed.** `bandit`'s `[[verify.checks]]` entry named `.scripts` and
  `.basicly/core/hooks` — the whole set when it was written. `.basicly/core/kit` arrived
  later, and nothing failed: a scan cannot notice a directory it was never pointed at,
  which is the one failure shape a green security gate hides. That directory is the least
  acceptable one to miss, since `basicly install` ships it into consumer repos and it runs
  in an agent spawn path.

  The kit is now a declared target, and because the target list is one a human maintains,
  a test sweeps the tracked Python under `.scripts` and `.basicly/core` and fails when any
  directory holding it sits outside the check — so the *next* such directory is caught
  here instead of inheriting the same silence. Coverage in the argv being necessary and not
  sufficient, a second test runs the declared command verbatim against a tree with an
  unsafe module in the kit and asserts it fails, with the same command minus the kit target
  over the same tree as the discriminator: that one passes, which is precisely the silent
  green being removed (`basicly-5gn2`).

## v0.6.0 - 2026-07-31

Delta: v0.5.1..v0.6.0

### Added

- **The parallel factory.** `basicly supervise` runs a standing supervisor that
  dispatches several beads concurrently, one worktree per lane, ranked by the
  tracker's scheduler and capped by configured concurrency. It records the score
  and rank behind every dispatch, meters each lane's context occupancy against
  the model's window, flags a stalled lane instead of waiting for the hard kill,
  cancels a lane whose merge a sibling landing broke, and carries a held lane to
  the next pass rather than re-dispatching it (`basicly-kjc5.5`,
  `basicly-kjc5.6`, `basicly-kjc5.7`, `basicly-vkh0.3`).
- **A serial merge queue.** Lanes land one at a time in dependency order.
  Conflicts are detected mechanically — no model sits in the merge path — and a
  colliding lane is bounced back to its owner alone, with the missed coupling
  attributed from the declared scopes rather than from landing order
  (`basicly-kjc5.32`).
- **A decision queue.** `basicly decisions`, `basicly decide` and `basicly
  answer` let a lane that cannot resolve a judgment park it for a human instead
  of guessing, and let a second session answer it (`basicly-kjc5.4`).
- **Autonomy grants, L0–L3, with a spend ceiling.** `basicly policy grant`
  issues a session grant that may resolve the checkpoints its level delegates,
  bounded by a token budget metered from issuance. The ceiling is enforced at
  dispatch admission, so a grant cannot overspend by racing (`basicly-kjc5.3`,
  `basicly-jr0l.15`, `basicly-jr0l.17`).
- **`basicly loop run`** drives a whole phase boundary from one command,
  resolving every checkpoint it is authorized to resolve on the way.
- **`basicly commit`** assembles the commit envelope from engine state, and
  **`basicly release`** automates a release up to (and not past) the annotated
  tag — it never pushes (`basicly-kjc5.42`).
- **Work sizing.** A working-set estimator and Definition-of-Ready governor size
  a package before dispatch; `basicly decompose --dry-run` reports the sizing
  band verdict, frozen against calibration drift; `basicly policy scaffold`
  prints the sections a work type owes (`basicly-kjc5.2`).
- **Cost and effort evidence.** Run records carry token telemetry read from each
  adapter's own usage report, a forecast-versus-actual rollup written onto the
  bead at ship, and the human wait time behind a session (`basicly-kjc5.1`,
  `basicly-kjc5.50`, `basicly-kjc5.51`).
- **A path-scoped rules tier.** A fragment may declare `paths:` and project to
  `.claude/rules/*.md`, activating only when a matching file is read — guidance
  that costs an always-on surface nothing (`basicly-a3ab.6`).
- **The invocation axis on skills**, a recall eval measured against a
  no-guidance control, and a committed ledger of the tracker surface the harness
  actually uses (`basicly-m4zv.1`, `basicly-agzx.1`, `basicly-vkh0.1`,
  `basicly-vkh0.2`).
- **`internal-info-scan`**, a hook that keeps internal-only identifiers out of
  committed content (`basicly-0n3d`).

### Changed

- **BREAKING: `invocation` is now a required field on every skill source.** Every
  `skill.yaml` must declare who can reach the entry: `model` for one the agent
  discovers and routes to, which keeps its `description` and pays context load
  every turn, or `user` for one only a human types, which carries no
  `description`. A source without the field fails `basicly catalog lint`.

  **Migration.** Add `invocation: model` to every `skill.yaml` you author. That
  one line is sufficient and preserves existing behaviour — before this change
  `description` was itself required, so any source that passed lint on v0.5.1
  already satisfies the model-invoked pairing rule and needs no second edit.
  Change an entry to `invocation: user` only when you also remove its
  `description`; nothing can route to a user-invoked entry, so a description
  there is context load bought for no reach.

  There is deliberately no default and no migration command. The field exists so
  that "does this entry route correctly" is a well-posed question, and a
  defaulted value would answer it by inertia rather than by declaration.

- **BREAKING: acceptance criteria are now required on every bead, including a
  `chore`.** The Definition-of-Ready check previously derived its required
  sections from the per-work-type template, and a `chore` was never asked for
  acceptance criteria. Every type is now asked, in either carrier — `br`'s
  structured `acceptance_criteria` field or an `## Acceptance Criteria` heading
  in the description body.

  **Migration.** An in-flight bead without them blocks at the classify
  checkpoint rather than failing loudly, so add them to anything already open:
  `br update <id> --acceptance-criteria "Given ... when ... then ..."`. The
  reason for the change is that a rubric's validate gate asks whether the change
  evidences its acceptance criteria, and a bead with none makes that gate read as
  green having proved nothing (`basicly-kjc5.36`).

- **BREAKING: two new hooks run on every commit.** `tracker-path-scan` refuses a
  tracker export carrying machine-specific absolute paths, and
  `internal-info-scan` refuses internal-only identifiers in committed content.
  Both are `always_run`, so a commit that passed on v0.5.1 can now fail
  (`basicly-vkh0.5`, `basicly-0n3d`).

  The `markdownlint` hook also changed how it starts: it now runs
  `.basicly/core/hooks/markdownlint.py`, which resolves node itself, instead of
  `npx --no-install markdownlint-cli2`. A hook shell has no profile, so with nvm
  off `PATH` a WSL interop lookup resolved `npx` to the Windows nodejs, which
  cannot express a worktree's UNC path. Re-run `basicly hooks-build` to pick up
  both (`basicly-jr0l.14`).

- **BREAKING: every rubric must carry at least one deterministic check.** A
  judged-only rubric is refused at load. Its gate could never fail — gate status
  is deterministic-first — so promoting it to required bought nothing and read as
  green having proved nothing. A consumer's judged-only rubric now fails
  `basicly catalog lint`; add a `verify_mode` or `command` check to it.

  In the same change a deterministic check gained a portable form: `verify_mode`
  runs the consumer repo's own configured verify checks instead of a fixed
  command. This matters because rubrics ship in the core catalog to every
  consumer, and the bug rubric's hardcoded `uv run pytest` would have answered
  "no" in any repo that is not this one (`basicly-kjc5.19`).

- **The ship phase derives only on evidence that the node landed.** A bead with a
  ship checkpoint recorded but no green required gate now derives a *lower* phase
  than it did before, so the next advance re-runs the landing instead of closing
  the bead. This re-interprets recorded tracker state, not just new work: a
  missing worktree binding used to mean "torn down after the merge", but a node
  that never built has no binding either, and an out-of-order ship approval
  therefore closed it with zero work done. The checkpoint prompt now also states
  that the merge has already happened and that approving publishes nothing
  (`basicly-k35r`, `basicly-jr0l.49`, `basicly-jr0l.39`).

- **Generated `SKILL.md` bytes differ per destination root.** A user-invoked
  skill projects with no `description` to `.claude/skills` (Claude loads it and
  still lists it by name) and with a short synthesized one to `.agents/skills`
  (codex rejects the file outright without the field). `basicly skills-check`
  reports drift until you re-run `basicly skills-build --all-default-roots`
  (`basicly-m4zv.10`).

- **`basicly verify --mode full` now runs the four projection gates locally.**
  They were CI-only, which left a fragment edit with no rebuild passing every
  local hook and reaching the remote stale. Verify can now fail where it passed
  (`basicly-m4zv.11`).

- **A check may declare `fix_command`.** When it does, the pre-commit hook
  applies the repair to staged files and `basicly verify --fix` applies it ahead
  of the checks, so a mechanically fixable failure is fixed rather than reported.
  Opt-in: a config without the key behaves as before (`basicly-kjc5.43`).

- **The codex adapter now passes `--sandbox workspace-write -a never`.** The
  sandbox is the safety boundary and `never` fails closed in headless exec, where
  there is no approver to escalate to. Note that the approval value shipped
  wrong for most of this range — `on-failure` is not in the CLI's enum, so every
  codex dispatch exited at argument parsing until it was fixed; `basicly runner
  dry-run` now validates both values against the installed CLI and names a
  rejected one (`basicly-t0kt`, `basicly-jr0l.36`, `basicly-jr0l.38`).

- **The pinned `br` version is stated in one place** and any drift from it warns
  once per process. It is a warning, not a gate — the harness still runs
  (`basicly-o7z5`).

### Fixed

- **A dispatch no longer hangs on inherited stdin.** `codex exec` reads
  additional input from stdin, so an arg-prompt dispatch blocked until the
  timeout. Stdin is now closed for it (`basicly-jr0l.36`).
- **A timed-out dispatch kills its whole process tree**, with a portable fallback
  signal, instead of leaving orphans behind.
- **An unreliable gate no longer spends a lane's rework budget or livelocks it.**
  A gate that fails for a known dependency defect is scored as unreliable and
  escalates rather than consuming an attempt (`basicly-55yh`, `basicly-jr0l.41`).
- **A `br` clock rejection is retried within a bounded deadline** and a
  chronically unreliable gate escalates (`basicly-jr0l.41`, `basicly-jr0l.42`).
- **The tracker export no longer commits machine-specific absolute paths**
  (`basicly-vkh0.5`).
- **A piped run stays observable**: stdout is line-buffered, so step lines are
  not withheld behind a block buffer (`basicly-8veb`).
- **A worktree is provisioned against the caller's repo root, not the process
  cwd**, and a worktree teardown keeps its telemetry by following the tracker
  redirect (`basicly-vkh0.8`).
- **Phase epics no longer gate their own children** (`basicly-axf1`), a
  decomposed child carries its parent's labels and priority
  (`basicly-jr0l.25`, `basicly-jr0l.26`), and an answered rework retry is
  executable (`basicly-4tjt`).
- **A skipped tracker-state commit is surfaced rather than omitted**
  (`basicly-f7li`), and the loop blocks when the tracker refuses the verify gate
  (`basicly-o7z5`).
- **`pytest` workers are capped** so the tracker's global write lock stops
  timing out under `-n auto` (`basicly-9s59`).
- **A confirm-code challenge says the caller may run it** once a human approves,
  instead of reading as "hand this over and wait" (`basicly-kjc5.34`).

## v0.5.1 - 2026-07-20

Delta: v0.5.0..v0.5.1

### Fixed

- **Install now activates git hooks on a fresh consumer repo**: hook activation
  runs pre-commit through `uv tool run` (uvx), which provisions the tool in an
  ephemeral environment, instead of `uv run`, which only resolved pre-commit when
  the consumer repo already declared it as a dependency and otherwise failed with
  "program not found". A target with no `.git` is now skipped with clear guidance
  (run `git init`, then `basicly hooks-build`) instead of an opaque pre-commit
  error, and the "run manually" hints point at `uvx pre-commit install`
  (basicly-x5gh).

## v0.5.0 - 2026-07-20

Delta: v0.4.0..v0.5.0

### Added

- **Per-agent health scoring and behavioral drift**: `basicly health [--json]
  [--window N] [--fleet]` derives a per-agent dispatch failure rate, a rework
  signal, and a bounded health score from the run-record log, and flags an agent
  whose recent failure rate regressed against a rolling baseline read off the
  log's own timestamps (basicly-y886).
- **Cross-repo fleet rollup**: `basicly status --fleet [--root PATH]` rolls each
  housed repo's status snapshot and run-record summary into one read-only JSON
  payload (basicly-h0f0).
- **Opt-in per-agent bot git identity**: a runner spec may pin a
  `git_name`/`git_email`; the dispatch seam commits the agent's work under that
  bot identity, and `identity-guard` validates the effective (env-aware) identity
  so a bot email is bound by the allow-email pattern (basicly-smzg).
- **Runner model field and attribution**: a runner adapter may pin a `model`,
  injected at the invocation seam and recorded in the run-record; landings and
  gate results carry the dispatched agent and model as `Harness-Runner` /
  `Harness-Model` attribution (basicly-45ld, basicly-140a).
- **Headless capability probe**: `auto` runner selection probes a candidate's
  headless flag before choosing it, so a renamed flag no longer gets picked and
  then fails at dispatch (basicly-bveo).
- **Action-boundary guardrails**: copilot deny-tool flags injected at dispatch
  (basicly-lqz5), captured runner output redacted for secret shapes at the source
  (basicly-3p2i), and a commit-time backstop blocking staged edits to generated
  files (basicly-yw28).
- **Human-checkpoint enforcement**: loop checkpoint approvals require an
  interactive terminal or a one-time confirm code, so a non-interactive process
  cannot self-approve ship (basicly-shgo).
- **Structured needs-input outcome**: a dispatched agent that cannot resolve a
  required fact writes a sentinel and the loop blocks instead of landing a guess
  (basicly-o774).
- **Agent-skills directories and skill taxonomy**: skills project as full
  agent-skills spec directories with optional frontmatter into both skill roots,
  split into universal core skills and technology-tagged optional skills (python,
  node, wsl) (basicly-q1w9 and children).
- **Structured acceptance-criteria for Definition of Ready**: the DoR gate
  accepts `br`'s structured `acceptance_criteria` field, not only a description
  heading (basicly-58iu).

### Fixed

- **Loop landing no longer strands uncommitted work**: a worktree whose build was
  not committed on its branch now blocks with clear guidance instead of
  misreporting a rebase conflict and burning rework attempts (basicly-4psl).
- **Ship refuses an unmerged worktree**: the ship transition blocks a node whose
  worktree branch has not landed, so a bead can no longer close with its code
  stranded (basicly-o0q3).
- **Pre-commit rewrite preserves unmanaged hooks**: projecting the managed hook
  block no longer drops a consumer's own comments or hook ordering (basicly-wd7u).
- **Windows path handling in the rubric runner**: a Windows executable path no
  longer breaks POSIX shell parsing on CI (basicly-5tjk).

## v0.4.0 - 2026-07-17

Delta: v0.3.1..v0.4.0

### Added

- **Per-run record at the dispatch seam**: every runner dispatch writes a
  metadata-only record keyed by bead id (agent, outcome, return code, duration,
  redacted command) to a self-ignored `.basicly/usage/run-records.json`
  (basicly-z6dh).
- **Catalog-managed agent deny-list**: a `permissions.yaml` catalog source
  projects a baseline Claude Code `deny` list into `.claude/settings.json`
  (`permissions build` / `permissions check`), and the repo dogfoods it
  (basicly-u0zg).
- **Stdlib secret-scan pre-commit gate**: a dependency-free hook scans staged
  added lines for common secret shapes, honoring a `pragma: allowlist secret`
  marker (basicly-yzyd).
- **Rubric-based behavioral eval**: `basicly rubric eval` runs YAML-authored
  rubric checks (deterministic first, judged advisory) and reports an advisory
  `rubric` gate (basicly-0122).

### Fixed

- **The loop no longer strands a commit**: `loop advance` refuses the build and
  ship transitions when run from a linked worktree, and worktree cleanup drops a
  session record whose branch is already gone (basicly-9niw).
- **Accurate tool-usage telemetry**: the counter no longer records
  backslash/dash heredoc bodies, flag-led pipeline segments, or inline
  `python -c` / `-m` code as tool names (basicly-v7eu).
- **Prefix-anchored commit id detection**: `beads-commit-msg` matches issue ids
  by the configured prefix (like `br`'s own commit scanner) instead of any
  hyphenated word, so ordinary phrases are never mis-flagged and the error names
  the real cause (basicly-jms0).
- **`.env` deny-list uses the form Claude Code accepts**: the guardrail keeps
  only the `Edit(...)` globs (which cover every file-mutation tool) and drops the
  `Write`/`MultiEdit`/`NotebookEdit` file rules Claude Code rejects at startup
  (basicly-7ihd).

## v0.3.1 - 2026-07-17

Delta: v0.3.0..v0.3.1

### Changed

- **CI runtimes bumped to Node 24**: every marketplace action pin
  (`actions/checkout`, `actions/setup-node`, `astral-sh/setup-uv`,
  `softprops/action-gh-release`) moved to its floating major that targets
  `node24`, clearing GitHub's Node 20 deprecation warning. No shipped-package
  change.

## v0.3.0 - 2026-07-17

Delta: v0.2.0..v0.3.0

### Changed

- **BREAKING — CLI namespace grouping**: the flat authoring and inspection
  subcommands moved under a `basicly catalog <verb>` group and the old names were
  removed (no aliases). `catalog-lint` → `catalog lint`, `catalog-verify` →
  `catalog verify`, `review` → `catalog review`, `list`/`skills-list`/`agents-list`
  → `catalog list [fragment|skill|agent]`, and
  `fragment-new`/`skills-new`/`agents-new` → `catalog new <fragment|skill|agent>`.
  The consumer projection pairs (`build`/`check`, `skills-build`/`skills-check`,
  `agents-build`/`agents-check`, `hooks-build`/`hooks-check`) and the harness
  commands stay top-level. Consumers who script the old names — including the
  scaffolded CI `catalog lint` step — must update them; re-run `basicly install`
  to refresh the scaffolded workflow.
- **Always-on size-warning cap raised to 9000** for the claude and copilot
  targets, calibrated to warn before the projected instruction files dilute
  attention rather than at an arbitrary round number; codex stays at 12000.
- **Every `br` invocation routes through one adapter seam**, giving tracker
  access a single, testable boundary.
- **Refreshed branding**: a redesigned logo and landing-page flow diagram.

### Added

- **`basicly status`**: a read-only snapshot of the harness/tracker/worktree
  state (with `--json`), safe to run anywhere — it never mutates and always
  exits 0.
- **`basicly usage`**: a report over the tool-usage telemetry, alongside a
  gitignored `basicly.local.toml` overlay that layers per-machine
  `[worktree]`/`[verify]`/`[policy]`/`[runner]` settings over the committed
  harness config.
- **Zero-touch tracker in loop worktrees**: worktrees share the base tracker
  through a `.beads/redirect` (capability probed at provisioning), and the engine
  owns tracker commits at provisioning, landing, and ship — agents no longer
  stage `.beads` on a harness branch.
- **Core-upgrade resilience**: the loader survives upgrades that remove a
  replaced fragment id and gates sources on `schema_version`.
- **OS-matrix release gating**: the release workflow runs on ubuntu/windows/macos
  with a fresh-repo install smoke test and attaches built wheels, and every
  release page now carries a copy-paste, tag-pinned `uvx` install command.
- **`session-finish` skill** and skill-invocation counting.
- **`hooks-check` diagnoses a missing `uv`**, and the committer requirements are
  documented in the README and CONTRIBUTING.

### Fixed

- **Harness-loop correctness**: hook scripts derive the repo root from cwd;
  staged and verify checks fail when the underlying `git` call fails; policy
  markers are matched token-exactly with a hook-floor compile test; the merge
  queue validates beads upfront, aborts failed merges, and guards dirty
  worktrees; co-owned writes are atomic with a byte-exact check and safe sweeps;
  the loop honors the configured base branch and concurrency cap; and
  `verify --issue` refuses to record a gate from a linked worktree so the landing
  advance records it from base.
- **Windows compatibility**: `basicly status` and the CLI degrade gracefully when
  `git` is absent from PATH, unencodable output is downgraded on narrow/cp1252
  consoles, unrunnable-command detection accepts the Windows "not found" detail,
  and the CLI test helpers stop stripping `PATH` from the subprocess env.
- **Tool-usage telemetry** counts only the real command at each quote-aware
  pipeline head — quoted-string bodies, flag values, and heredoc bodies are no
  longer miscounted as tools.
- **commit-msg** now names the offending character when a description is
  rejected, and the `conventional-commits` skill documents the lowercase-only
  charset (put version numbers and proper nouns in the body).
- **CI hygiene**: tracker-only pushes no longer trigger builds, the pytest gate
  runs in parallel via xdist (dropping a duplicate pre-commit step), workflow
  jobs have descriptive names, and the usage-report tests are hermetic against
  live telemetry.

## v0.2.0 - 2026-07-16

Delta: v0.1.3..v0.2.0

### Added

- **Tool-usage telemetry hook**: a PostToolUse hook for both Claude Code and
  GitHub Copilot counts every shell command's pipeline heads into
  `.basicly/usage/tool-usage.json` (self-ignored from git) — token-free,
  deterministic data on which terminal tools agents actually use, for tailoring
  the catalog with real evidence. Ships in the catalog and is dogfooded here.
- **Copilot hook manager**: `hooks.yaml` entries now target one of three
  managers — `git` (pre-commit config), `claude` (`.claude/settings.json`, with
  per-spec event and matcher), or `copilot` (managed
  `.github/hooks/basicly-<id>.json` files, synced and pruned like every other
  projection).
- **Runner auto-dispatch in the harness loop**: `basicly loop advance` on a
  ready leaf provisions the worktree and dispatches the selected headless
  runner inside it; the `manual` runner preserves the block-and-resume handoff
  (this repo pins `[runner] default = "manual"`).
- **Bootstrap shims**: `.scripts/bootstrap.sh` (curl-able POSIX sh) and
  `.scripts/bootstrap.ps1` install `uv` when absent, then run the pinned
  install — one command on a machine with no Python at all.
- **Rich terminal output**: styled status lines, real tables, and `--help`
  grouped by audience (consumer / contributor / harness); piped and CI output
  stays byte-identical plain text. Adds `rich` as a runtime dependency.
- **Branding and a landing page**: a project logo, README badges, a
  GitHub-rendered architecture diagram, a root `CONTRIBUTING.md`, and a
  GitHub Pages site at <https://niksavis.github.io/basicly/>.

### Changed

- **README rewritten user-first**: overview → quick start (copy-pasteable
  install, upgrade, uninstall) → reference; `PYTHONPATH=` relics removed, every
  flag explained, hook stages vs the pre-commit framework filename clarified.
- **architecture.md now describes shipped behavior plainly**: implementation
  status markers were removed everywhere except the genuinely deferred items,
  which are collected in one section.
- `.claude/settings.json` is committed: the deny-list is tracked in git and
  carries the tool-usage hook wiring.

## v0.1.3 - 2026-07-16

Delta: v0.1.2..v0.1.3

### Added

- **Technology scoping for the catalog**: sources (skills, fragments, agents,
  hooks) may declare `technologies: [python, zsh, ...]`; an untagged source is
  universal and always ships. `basicly install --technologies python,zsh`
  records the selection under `[catalog]` in `basicly.toml`; the projection
  commands then skip non-matching sources, previously projected skills/agents
  the selection excludes are pruned, and excluded managed hooks are stripped
  from `.pre-commit-config.yaml` and `.claude/settings.json`. The tag
  vocabulary is a controlled list enforced by `catalog-lint` and every loader,
  and the stack-specific skills (`tool-uv`, `tool-zsh`, `tool-tmux`,
  `tool-starship`, `tool-wezterm`) are tagged. With no selection recorded the
  full catalog ships, exactly as before.
- **Agents as a catalog kind**: subagents are authored as composable
  `agent.yaml` sources plus shared `*.block.yaml` building blocks, projected to
  `.claude/agents/` with schema validation, composition lint (unknown block
  refs, read-only postures granting write tools, portable size cap), and
  uninstall sweep. Three core agents ship: `code-reviewer`, `test-runner`,
  `security-auditor`.
- **A `quirks` fragment category** wired to the self-improvement retro: one
  real incident, one bullet (environment/timing/platform traps).

### Changed

- **Scoped rules are single-sourced**: the Copilot `scoped_instructions`
  output was retired in favor of one scoped-rules source per target, and
  `basicly build` now sweeps manifest-tracked outputs that drop out of the
  plan, so retiring an output converges consumers instead of stranding stale
  projections.
- The committed Claude settings deny `.env*` writes in addition to reads, and
  catalog guidance was pruned/tightened to fit projection size advisories.

### Fixed

- **Feature fan-in no longer collides with self-landed children**: a parent
  feature whose children each landed and closed through their own loop
  advances build -> verify instead of failing with "no worktree session
  named"; already-merged, torn-down children count as landed.
- Projected instruction files render lint-clean (their markdownlint ignores
  were dropped), and new worktrees receive uncommitted tracker state so the
  first in-worktree commit does not trip the beads hook.

## v0.1.2 - 2026-07-16

Delta: v0.1.1..v0.1.2

### Fixed

- **Release tags could ship stale package metadata**: the v0.1.1 tag was cut
  without a version bump, so `basicly --version` at that tag prints `0.1.0`
  and consumer `install.json` files get stamped with the stale
  `basicly_version`, breaking version-based upgrade/drift detection. The
  package version is now single-sourced from `src/basicly/__init__.py`
  (hatchling dynamic version) so `pyproject.toml` and the module can no
  longer drift, and it is correctly bumped for this release. The v0.1.1 tag
  itself is left untouched; re-running `basicly install` at this tag
  refreshes a consumer's recorded version.

### Added

- **Release gate for version mismatches**: the release workflow now fails
  before publishing when the pushed tag name and the package version
  disagree, so a tag can no longer ship mismatched metadata.

## v0.1.1 - 2026-07-16

Delta: v0.1.0..v0.1.1 (documentation-only patch)

### Changed

- **`tool-br` skill**: new Common Pitfalls bullet — never commit with a guessed
  issue id; `br create` assigns a random base, so run it alone, read the
  generated id from its output, and commit separately (chaining with `|| true`
  silently swallows the hook rejection).
- **`conventional-commits` skill**: description rule now states that version
  strings and filenames (dots/uppercase, e.g. a tag name or `AGENTS.md`) can
  never appear verbatim in a commit description and must be reworded, with a
  matching invalid example.

### Added

- The full agent-file state-of-the-art research report (building-blocks table,
  phrasing rules, determinism ledger, prioritized recommendations, source
  evaluations) is persisted as a comment on epic `basicly-84v` in the tracker.

## v0.1.0 - 2026-07-15

Delta: initial..v0.1.0

### Highlights

- **One-command lifecycle**: `basicly install` performs first install *and* every
  upgrade (idempotent converge: managed core sync with provenance guards,
  overlay + `basicly.toml` scaffolding that never overwrites user content, then
  fragment/skill/hook projection with git-hook activation). `basicly uninstall
  [--purge]` is the inverse. Install also initializes a beads (`br`) tracker
  workspace with a repo-derived prefix, scaffolds VS Code tasks
  (build/skills-build/hooks-build/update/uninstall) and a consumer CI gates
  workflow (`.github/workflows/basicly-gates.yml`).
- **Complete harness loop**: `basicly loop` drives tracked issues through
  intake → classify → build → verify → ship with engine-enforced human
  checkpoints, isolated sibling git worktrees per track, a serial merge queue,
  and a bounded rework policy — all state lives in the `br` tracker.
- **Deterministic gates, consumer-appropriate**: the shipped pre-commit/pre-push
  hooks run whatever `[[verify.checks]]` each repo configures (fast at commit,
  full at push) instead of a hard-coded stack; commit messages are gated on
  Conventional Commits + a tracked beads issue id; `catalog-lint` and
  markdownlint round out the local + CI floor. A repo with no checks configured
  is never blocked by tooling it lacks.
- **Curated catalog**: 26 skills, 17 always-on/scoped fragments, and the hook
  set project from YAML sources into each agent's native format — `CLAUDE.md` +
  `.claude/rules`, `AGENTS.md` (Codex, verified against July 2026 capabilities),
  `copilot-instructions.md` + `.github/instructions`, and skills into
  `.claude/skills` + `.agents/skills` (the `.github/skills` copy was dropped:
  Copilot reads all roots, so it only tripled discovery).
- **Customization without forking**: consumer overlays add or override
  (`override: true` + `replaces`) any core fragment from
  `.basicly-local/fragments/user/`; upgrades keep them byte-for-byte.
- **Validated end-to-end** in the `terminal` repo (first real consumer):
  install → customize → upgrade → uninstall/reinstall round-trip → a full
  harness-loop track, with every defect found during the run fixed in this
  release.

### Changed

- **BREAKING (CLI):** `basicly install` replaces `init` and `update` — one
  idempotent converge command performs first install *and* every upgrade
  (materialize the bundled catalog, scaffold overlay + `basicly.toml` without
  overwriting user content, then `build` + `skills-build` + `hooks-build` with
  hook activation). The legacy-layout migration and legacy-source pruning that
  `update` performed now run inside `install`.
- Upgrades really sync the managed core now: a repeat `install` overwrites core
  files changed upstream, deletes files the bundle no longer ships, and — using
  the provenance snapshot — keeps hand-edited core files with a warning
  (`--force` overwrites them); files of unknown origin are never deleted. The
  overlay and `basicly.toml` are untouched. `hooks-build` no longer copies hook
  scripts (core content is owned by `install`) and errors when the core was
  never materialized.
- **BREAKING (catalog source format):** catalog content is now authored as YAML
  sources — skills as `core/skills/<slug>/skill.yaml` and fragments as
  `core/fragments/**/<id>.fragment.yaml` — instead of the discoverable `SKILL.md`
  and `*.fragment.md` names. The projectors render the agent-loaded `.md` files
  (`SKILL.md`, `CLAUDE.md`, `AGENTS.md`, `copilot-instructions.md`, rules and
  instructions) at the target roots only, so a broadly-scanning agent can no
  longer double-load a skill. Rendered output is unchanged except for a
  "generated" marker on projected `SKILL.md` files.

### Added

- `basicly uninstall [--purge]`: removes everything basicly manages (core,
  state, manifest-listed generated files, projected skills carrying the
  generated marker, and the managed pre-commit block — deleting the config and
  uninstalling the git hooks when nothing else remains). The overlay and
  `basicly.toml` survive unless `--purge`; the authoring repo refuses.
- Install provenance: `basicly install` writes `.basicly/state/install.json`
  (basicly version, timestamp, per-file sha256 snapshot of the managed core as
  materialized), and `basicly check` reports hand-edited/removed core files and
  an installed-vs-current version mismatch as advisory notes. The authoring
  repo records no state.
- JSON Schemas for skill and fragment sources (`core/schemas/`), referenced from
  each source via a `# yaml-language-server` header for editor/agent validation.
- `catalog-authoring` skill and an always-on authoring fragment covering how to
  write and project catalog sources.
- `basicly skills-new` and `basicly fragment-new` scaffold commands.
- `basicly catalog-lint` gate (schema validation, no `.md`-named sources, single
  `.yaml` extension), wired as a pre-commit hook and a CI step.

### Migration

- `basicly install` prunes legacy discoverable-name sources (`SKILL.md`,
  `*.fragment.md`) from the managed core, so installing basicly over a
  pre-migration hand-copied catalog cleans up the old sources automatically. The
  user overlay (`.basicly-local/`) is never touched.

### Commit delta (auto-generated)

- docs(readme): add the pinned install command for the release (basicly-zrj.16) (8fec978)
- chore(beads): close the skills root-drop (basicly-sqn) (f02f4ed)
- feat(skills)!: drop the github-skills projection root to stop copilot triple discovery (basicly-sqn) (c7e2685)
- chore(beads): record sqn claim (basicly-sqn) (d1cb968)
- chore(beads): file the skills root-drop task and copilot dedup question (basicly-sqn) (e52f04a)
- chore(beads): close the terminal acceptance with the full run writeup (basicly-zrj.15) (b1c6b39)
- chore(beads): file the loop tracker-state race (basicly-djt) (4ca1e54)
- chore(beads): close the catalog-lint ladder fix (basicly-7o8) (e2087f6)
- fix(hooks): resolve the catalog-lint cli through a consumer-safe ladder (basicly-7o8) (37b72e6)
- chore(beads): record 7o8 claim (basicly-7o8) (6b7198a)
- chore(beads): file the consumer catalog-lint hook bug (basicly-7o8) (30b1616)
- chore(beads): close the legacy overlay warning fix (basicly-v1y) (dfbb868)
- fix(loader): warn loudly when legacy fragment-md sources are present (basicly-v1y) (20c0498)
- chore(beads): record v1y claim (basicly-v1y) (db83055)
- chore(beads): file the silent overlay legacy-md ignore bug (basicly-v1y) (760e427)
- chore(beads): close the legacy engine migration fix (basicly-u9o) (a5392a9)
- fix(cli): remove the legacy vendored engine dir during install migration (basicly-u9o) (245e758)
- chore(beads): record u9o claim (basicly-u9o) (7f7afff)
- chore(beads): file the legacy engine dir migration gap (basicly-u9o) (bb30672)
- chore(beads): close the consumer ci workflow scaffold (basicly-7kh) (df1f987)
- feat(cli): scaffold a consumer ci gates workflow on install (basicly-7kh) (0dc3e87)
- chore(beads): record 7kh claim (basicly-7kh) (5317d6d)
- chore(beads): file the agent-file sota adoption epic and children (basicly-84v) (59ae564)
- chore(beads): close the config-driven hooks fix (basicly-yp3) (29dba7e)
- fix(hooks)!: pre-commit and pre-push run configured verify checks not a hard-coded stack (basicly-yp3) (82e05d2)
- chore(beads): record yp3 claim (basicly-yp3) (9c1c6d7)
- chore(beads): file the config-driven hooks bug and consumer ci workflow feature (basicly-yp3) (46c3aeb)
- chore(beads): close the lockfile rename fix (basicly-cjb) (c82e3e2)
- fix(build): pin the npm package name so worktree installs stop renaming the lockfile (basicly-cjb) (48282b7)
- chore(beads): record cjb claim and dor rewrite (basicly-cjb) (5fdefcc)
- chore(beads): close the vscode tasks scaffold (basicly-0eo) (9fbdcfd)
- feat(cli): scaffold vscode tasks for the harness operations on install (basicly-0eo) (7681cbc)
- chore(beads): record 0eo claim (basicly-0eo) (80be717)
- chore(beads): record 0eo filing (basicly-0eo) (aa54225)
- chore(beads): close the install beads-init feature (basicly-em9) (7b99899)
- feat(cli): install initializes the beads workspace with a derived prefix (basicly-em9) (5af233b)
- chore(beads): record em9 filing and claim (basicly-em9) (73a67aa)
- chore(beads): close the codex reassessment (basicly-joj) (67ce2ba)
- docs(architecture): correct codex capabilities and set codex cap allowance (basicly-joj) (2d04834)
- chore(beads): record joj claim and verified codex research (basicly-joj) (c22583e)
- chore(beads): file the projector markdownlint cleanliness follow-up (basicly-gdi) (236aa2e)
- chore(beads): close the markdownlint gate wiring (basicly-4j0) (194cb53)
- chore(hooks): wire markdownlint-cli2 into pre-commit and ci (basicly-4j0) (6899497)
- chore(beads): record 4j0 claim and worktree binding (basicly-4j0) (f09ce5f)
- docs(architecture): unwrap line rendering as accidental plus-list (basicly-4j0) (1a54f36)
- chore(beads): file the worktree package-lock rename bug (basicly-cjb) (a12f893)
- chore(beads): close the obsolete copilot size-cap split issue (basicly-4ce) (107a42a)
- chore(beads): close the consumer robustness epic (basicly-zrj.13) (cb364ae)
- chore(beads): close the verify runner robustness fix (basicly-zrj.13.2) (0dd9e20)
- fix(verify): fail cleanly on unrunnable check commands and stop scaffolding python-only checks (basicly-zrj.13.2) (725c8b3)
- chore(beads): record zrj-13-2 claim scaffold decision and dor rewrite (basicly-zrj.13.2) (1456c69)
- chore(beads): close the beads hook workspace skip fix (basicly-zrj.13.1) (c7320e2)
- fix(hooks): skip beads id check cleanly when no workspace exists (basicly-zrj.13.1) (ea1f6e0)
- chore(beads): record zrj-13-1 claim and dor rewrite (basicly-zrj.13.1) (4df376f)
- chore(beads): close the worktree hook clobber fix (basicly-zrj.13.3) (2f72656)
- fix(worktree): reinstall base checkout hooks on teardown (basicly-zrj.13.3) (898952a)
- chore(beads): record zrj-13-3 claim and dor rewrite (basicly-zrj.13.3) (a991c87)
- chore(beads): close the pushed-ref install verification (basicly-zrj.14) (0da8e63)
- docs(architecture): record verified pushed-ref uvx install (basicly-zrj.14) (95384cd)
- chore(beads): record zrj-14 claim and worktree binding (basicly-zrj.14) (8d60b4f)
- chore(beads): prune orphaned duplicate issue and normalize tombstones (basicly-joj) (1f5ce62)
- chore(beads): recover the agents-md cap reassessment issue lost in reconcile (basicly-joj) (7141402)
- chore(beads): close the lifecycle epic and set the next pickup (basicly-zrj.12) (afed186)
- feat(cli): add basicly uninstall for clean removal (basicly-zrj.12.3) (e7ccc3e)
- chore(beads): record uninstall claim and dor rewrite (basicly-zrj.12.3) (34c4c18)
- feat(cli): provenance-guarded core upgrade sync in install (basicly-zrj.12.2) (ebe2f67)
- chore(beads): record core sync claim and dor rewrite (basicly-zrj.12.2) (b26c20f)
- feat(state): record install provenance and report drift in check (basicly-8fg) (f9ff97a)
- chore(beads): record 8fg claim and dor rewrite (basicly-8fg) (83d80ca)
- chore(beads): close the install task and file the worktree hook clobber bug (basicly-zrj.12.1) (c16ede2)
- feat(cli)!: replace init and update with one-command install (basicly-zrj.12.1) (9269575)
- chore(beads): record lifecycle claims and progress notes (basicly-zrj.12) (e773393)
- docs(architecture): redesign lifecycle around one-command install and uninstall (basicly-zrj.12.4) (943d499)
- chore: close fv6 and mark basicly-8fg as the next pickup (ca52c25)
- docs(catalog): resolve dependency-confirmation and test-command ambiguities (35f809f)
- chore: close the oversized-fragments issue (4856b92)
- docs(catalog): dedupe and reframe repeated always-on guidance (763d37c)
- chore: record lce progress and ship-decision note (b721559)
- docs(catalog): tighten oversized always-on fragments under the 8000-char cap (8243d7e)
- chore: close the semantic-review issue (acef9a5)
- feat(review): add advisory agent-assisted semantic review command (357b55f)
- chore: close the projection-unification issue (8f530a3)
- refactor: unify skills hooks and build onto a shared projection engine (8dfebc1)
- chore: close the catalog-verify issue (fb2b97f)
- feat(catalog): add catalog-verify content checks and build --verify (9a9eea7)
- chore: close the enforced-by lint issue (a137619)
- feat(catalog): add enforced-by field and enforcement-pointer lint (233419e)
- chore: close git-hook-gates umbrella and mark next task (9374ba5)
- chore: close the quality-gate verification rule issue (311cf32)
- docs: strengthen the quality-gate verification rule (cc17cdb)
- feat(catalog): prune legacy sources on basicly update (3398d41)
- docs: record the catalog yaml source migration (6ba2361)
- feat(catalog): add catalog-lint gate with pre-commit hook and ci (668b9b0)
- feat(catalog): add yaml source schemas authoring skill and scaffolds (bfa7fd9)
- feat(fragments): author fragments as yaml sources (20aa7cb)
- feat(skills): author skills as yaml sources rendered to target roots (1040009)
- chore: plan the catalog yaml source migration epic (54c924e)
- feat(loop): add agent-agnostic runner adapters (7c53d00)
- feat(loop): author projected harness-loop guidance (e357427)
- chore: plan the projected orchestration guidance session (bd3317f)
- feat(loop): wire the basicly loop cli (5c18f41)
- chore: record the resume pointer for the loop cli child (3d1f5cb)
- feat(loop): add the checkpoint-gated loop state machine (5b41a30)
- chore: plan the loop state machine session (63bc631)
- fix(ci): validate the full commit message in the commit-messages gate (bb172c5)
- feat(loop): add the classify step (0ec4158)
- chore: plan the classify-step session (5b5f3e9)
- feat(loop): add the resumable loop state model (0616b22)
- chore: plan the loop engine decompose-first session (e7a75a8)
- feat(decompose): add the feature decomposer and dependency graph builder (1138657)
- chore: record next-session plan for the decomposer (basicly-onb.4) (7279c41)
- chore: close the merge orchestrator feature (basicly-onb.5) (e6d2d88)
- feat(merge): add serial merge orchestrator for harness worktrees (4894974)
- chore: record next-session plan for the merge orchestrator (basicly-onb.5) (4010f4a)
- chore: close the gate policy engine feature (basicly-onb.3) (23feb87)
- feat(policy): add gate and checkpoint policy engine (221ddd6)
- chore: record next-session plan for the gate policy engine (basicly-onb.3) (4302ddc)
- chore: close the verify runner feature (basicly-onb.2) (8aa77e1)
- feat(verify): add config-driven verify runner with br gate reporting (273abd1)
- chore: record next-session plan for the verify runner (basicly-onb.2) (b401f87)
- chore: close the work-isolation feature and its tasks (basicly-onb.1) (da90df9)
- docs(skills): add agent-agnostic worktree-isolation skill (f0c285a)
- feat(worktree): add consent-gated claude bg-isolation setting (28a5dbd)
- test(worktree): cover provision command selection and base-untouched (8252551)
- chore: record next-session findings for the worktree isolation tasks (basicly-onb.1) (d809c57)
- chore: record worktree isolation task closures (basicly-onb.1) (b54109c)
- feat(worktree): add worktree cli subcommands and config (efdaa08)
- feat(worktree): add worktree cleanup and teardown (552e575)
- feat(worktree): add sibling worktree create and provision (74ff8e5)
- chore: set the committed project settings as the bg-isolation install target in the plan (basicly-onb.1.6) (0a6e175)
- chore: track the claude bg-isolation install step in the harness plan (basicly-onb.1.6) (cc1c76f)
- chore: plan the harness epic with feature and task tree (basicly-onb) (b0f2225)
- docs: specify the harness in architecture and fill tool-br skill gaps (basicly-43l) (f38c1c4)
- chore: add committed trusted-workstation claude permissions (basicly-oda) (caf01d2)
- feat: activate git hooks on hooks-build and flag uninstalled gates (basicly-ed2.3) (17df629)
- feat: ship identity-guard in the hooks manifest (basicly-ed2.2) (3a7267e)
- fix: accept dotted beads ids in commit-msg gate and align its skill (basicly-ed2.1) (0ffe253)
- feat: enforce replaces and override validation on fragment load (basicly-q49) (6514b01)
- docs: warn against hand-rolled bulk-create loops in tool-br skill (basicly-f3m) (a77cdec)
- feat: add dogfood-gate and verification-scope rules to quality gate (basicly-zrc) (6baf504)
- docs: align section 9 with implemented init and honest git install verification (basicly-zrj.11) (7e02fa4)
- fix: prefer source catalog over stale packaged copies and dedup the walker (basicly-zrj.10) (43f8d7b)
- fix: resolve one core root from config for init and hooks (basicly-zrj.8) (ef846bc)
- fix: quote hook script path in pre-commit entry string (basicly-zrj.9) (34bebb0)
- fix: compare and edit only managed hooks in pre-commit config (basicly-zrj.7) (b9ac894)
- docs: mark init and hooks projection implemented and close gates epic (basicly-zrj.3) (13a4833)
- feat: add hooks-build and hooks-check to install the gate hooks (basicly-lku, basicly-t51) (cb787dd)
- feat: add basicly init to scaffold a consumer repo (basicly-xwt) (a2737ca)
- chore: close the packaging epic after all children complete (basicly-zrj.1) (db3c816)
- docs: mark packaging resolved and document the uvx install flow (basicly-8u2) (d1cf4ec)
- feat: bundle core catalog into the package for init to materialize (basicly-juj) (e2d1623)
- build: enable packaging with hatchling backend (basicly-8a7) (251a810)
- build(deps): promote jinja2 to a runtime dependency (basicly-8if) (e2d59b5)
- chore: break down the initial release roadmap into beads epics and tasks (basicly-zrj) (72e5c96)
- feat: add generic git identity guard hook and per-host identity setup tooling (basicly-4on) (92f9efa)
- feat: exclude scoped fragments from baselines and refresh agent config catalog (basicly-0e9) (e3df46d)
- chore: pin beads prefix and ignore transient br artifacts and document gotchas (basicly-77f) (247a7bf)
- docs: close beads issues before the resolving commit not after (basicly-fcl) (3bcd369)
- chore: close basicly-9j9 (basicly-9j9) (08e5e6a)
- fix: sort imports in test-loader and test-skills for ruff i001 (basicly-9j9) (de95c83)
- chore: close basicly-akn (basicly-akn) (eccbc38)
- feat: harden commit-msg description rules and add self-improvement retro fragment (basicly-akn) (18040c3)
- chore: close basicly-1da (basicly-1da) (95f613b)
- fix: stop cli integration tests from mutating the real repo manifest (basicly-1da) (727af06)
- chore: close basicly-sr2 (basicly-sr2) (c9c7c40)
- fix: clarify description must be entirely lowercase in commit-msg hook and skill (basicly-sr2) (25f4e7b)
- feat: support conventional commits breaking-change marker and add commit skill (basicly-sr2) (404adab)
- feat: add basicly harness distribution engine and fragment catalog (basicly-7ph, basicly-idr) (0220a35)

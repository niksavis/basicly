# Changelog

All notable changes to this project are documented here. The format is based on
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/), and this project adheres
to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

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

- **The tier injection kit is documented**, in `.basicly/core/kit/README.md` (how to
  use it) and `docs/design/tier-injection-kit.md` (why it is shaped this way). They
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

### Fixed

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

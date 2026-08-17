# Archify — Evaluation for Interactive Software-Factory Dashboards

Reviewed 2026-08-17 against `tt-a1i/archify` at commit `e1ac748f19cf805e44bf74fb93c796662152e273`,
release `v2.15.0`, cloned to `/home/niksa/development/reference-repos/archify`. Every claim below
was produced by running the tool against **this repository's real backlog**, not by reading its
README. Probe artifacts are disposable and live outside the tree.

This document is **findings, not a plan.**

## 0. Verdict

**Reject for the dashboard; adopt-later, narrowly, for architecture illustration.** The single
fact that decides it: **archify's JSON IR is a closed schema — every object sets
`"additionalProperties": false` — whose node types are the seven-value infrastructure enum
`frontend · backend · database · cloud · security · messagebus · external`, with no field
anywhere for a status, a count, a priority or a state** (`archify/schemas/common.schema.json:21`
for the enum; `additionalProperties: false` on every node object, e.g.
`archify/schemas/workflow.schema.json:223`). A backlog record's status is the one thing the
format cannot carry, so the "live work tracking" half of the request is not a mapping problem
that an adapter solves — it is outside the data model.

The vendor agrees. `PRODUCT.md:24` lists **"Dense dashboard shells, endless identical card
grids"** under *Anti-references*, and `PRODUCT.md:23` rejects "motion-first graph demos that
imply relationships or activity not present in the authored source." Archify is deliberately
designed **against** the thing the owner asked for.

## 1. Licence — no obstacle

| Fact | Value | Evidence |
| --- | --- | --- |
| SPDX identifier | **MIT** | `/home/niksa/development/reference-repos/archify/LICENSE:1`; GitHub API `license.spdx_id` = `MIT`, fetched 2026-08-17 |
| Second licence file | identical MIT, ships inside the skill payload | `archify/LICENSE` |
| Declared in manifest | `"license": "MIT"` | `archify/package.json:7` |
| Provenance note | dual copyright: `2026 tt-a1i (Archify)` **and** `2025 Cocoon AI (original "architecture-diagram-generator")` | `LICENSE:3-4` |

MIT permits use, modification and redistribution with attribution. **This is not a hard stop.**
The one thing to carry forward is the dual copyright: archify is a rename/fork of Cocoon AI's
"architecture-diagram-generator", so any vendoring must reproduce **both** notices, not just
tt-a1i's. I did not locate the upstream Cocoon AI repository — see §11.

## 2. Maintenance — healthy, but effectively one person

| Signal | Value | How measured |
| --- | --- | --- |
| Last commit | `2026-08-17` (same day as this review) | `git log -1` on the clone |
| Last push | `2026-08-17T15:47:26Z` | GitHub API `pushed_at` |
| Releases | 19 tagged, first `2.0.0` on 2026-04-15 | `rg -n '^## \[' CHANGELOG.md \| wc -l` |
| Cadence | 11 releases in the ~10 weeks from 2026-06 to 2026-08 | `rg -n '^## \[' CHANGELOG.md \| rg '2026-0[678]'` |
| Repo age | created `2026-04-15T05:27:37Z` — **four months old** | GitHub API `created_at` |
| Open issues | **13** (excluding PRs) | `gh api 'repos/.../issues?state=open' --jq '[.[]\|select(.pull_request==null)]\|length'` |
| Open PRs | 6 | `gh api 'repos/.../pulls?state=open'` |
| Closed issues | 24 | `gh api 'search/issues?q=...type:issue+state:closed'` |
| Contributors | **7, of whom one has 139 commits and the other six have 1–2 each** | `gh api 'repos/tt-a1i/archify/contributors'` |
| Stars / forks | 13 835 / 1 014 | GitHub API, fetched 2026-08-17 |
| Archived | no | GitHub API `archived: false` |

Read honestly: the project is active and popular, but it is **four months old and a single-author
project** (139 of ~148 attributed commits). The six other contributors total 9 commits. A
four-month-old bus-factor-one dependency on a consumer-facing surface is a real risk, though the
MIT licence and the self-contained output blunt it — a generated artifact keeps working if the
project stops.

## 3. Stack — a Node CLI that emits one HTML file

- **Language / runtime:** JavaScript ESM, `"type": "module"`, `engines.node >= 18`
  (`archify/package.json:10-13`). Verified running under Node `v24.18.0`.
- **Build system:** none. It is plain `.mjs` run directly; `bin.archify` →
  `./bin/archify.mjs` (`archify/package.json:8-10`).
- **Runtime dependencies:** **zero.** The only entries are `devDependencies`: `ajv ^8.17.1`
  and `simple-icons 16.28.0` (`archify/package.json:27-30`), and validators are pre-generated
  into `renderers/shared/generated-validators.mjs` (`archify/package.json:20`). This is a
  genuine strength.
- **Renders with:** hand-emitted **SVG inside a single HTML file**, not a charting library.
  Confirmed by rendering: the artifact carries 3 `<script>` and 1 `<style>` block and an
  `<svg viewBox=...>`.
- **Distribution:** an *agent skill*, installed with `npx skills add tt-a1i/archify -g`
  (`README.md:38`), driven by `archify/SKILL.md`. The intended user is an LLM authoring JSON,
  not a program generating it. This matters for §5.
- **Not fully self-contained:** the README calls the output "one file, ready to trust and share"
  (`README.md:17`), but the rendered artifact fetches a webfont from
  `https://fonts.googleapis.com/css2?family=JetBrains+Mono...` and preconnects to
  `fonts.gstatic.com`. There is a local font-family fallback chain, so it degrades, but it is
  **not** offline-pure and it phones home to Google on open.

Commands available (`node bin/archify.mjs --help`, run 2026-08-17): `render`, `compare`,
`deliver`, `preview`, `validate`, `inspect`, `check`, `visual-check`, `guide`, `brands`,
`examples`, `doctor`, `demo`. Types: `architecture, workflow, sequence, dataflow, lifecycle`.

## 4. Our side of the contract

`basicly tracker` already emits exactly the JSON an adapter would read. Measured 2026-08-17 on
this repo:

```text
stats  → {"records": 934, "by_status": {"blocked": 2, "closed": 709, "deferred": 3, "open": 220},
          "ready": 168, "blocked": 54, "tombstoned": 0}
```

- `ready` returns `{schema, sort, count, records:[{rank, score, record, title}]}` —
  `.basicly/core/kit/tracker/queries.py:91-106`
- `blocked` returns `{count, records:[{record, status, blocked_by:[{record,status}], children:[...]}]}` —
  `.basicly/core/kit/tracker/queries.py:109-132`
- `stats` returns counts by status plus ready/blocked — `.basicly/core/kit/tracker/queries.py:149-167`
- CLI wrappers: `src/basicly/tracker_query.py:46` (`cmd_ready`), `:66` (`cmd_blocked`),
  `:89` (`cmd_stats`), `:107` (`cmd_show`), `:122` (`cmd_list`)

So the data is graph-shaped (`blocked_by` and `children` are real edges), it is **935 records
wide**, and every record's most important attribute is its **status**.

## 5. The input contract — the load-bearing section

I attacked the claim *"archify can render our live backlog"* by building an adapter and running
the real validator. **Positive control first:** archify's own shipped example validates clean —
`node bin/archify.mjs validate workflow examples/agent-tool-call.workflow.json` → exit 0,
`ok workflow ... (9 artifact checks)`. So the harness works and the failures below are properties
of the input, not of my probe.

### 5.1 The format is authored layout, not bound data

Every diagram type demands that the **author place each node by hand**:

| Type | Placement requirement | Evidence |
| --- | --- | --- |
| `workflow` | each node requires `lane` **and** `col`, `col` capped at **0–5** | `schemas/workflow.schema.json`, node `required: [id, lane, col, type, label]` |
| `lifecycle` | requires `lane` + `col` (`col` max **4**), and **`lanes` is capped at `maxItems: 4`** | `schemas/lifecycle.schema.json:101-104` |
| `architecture` | requires `pos: [x,y]` pixels, or `row`/`col` under `layout.mode: "grid"` with `cols` max **12** | proved by probe: omitting them gives `Component "ledger" must include pos [x, y] when layout.mode is omitted (free placement)` |

There is **no automatic layout engine**. That is the architectural difference from every graph
library, and it is the reason a 935-record backlog cannot be "pointed at" the tool.

### 5.2 There is nowhere to put a status

- Node objects are `additionalProperties: false`, so a consumer **cannot** add `status`,
  `priority`, `rank` or `count`. Adding one is a validation error, not an ignored extra.
- The only node classifier is `componentType`, a closed 7-value **infrastructure** enum
  (`schemas/common.schema.json:21`). `lifecycle` has a slightly better 8-value state enum
  (`start, active, waiting, decision, success, failure, neutral, external`,
  `schemas/lifecycle.schema.json`) — but `lifecycle` is capped at 4 lanes.
- The only free-text carriers are `label`, `sublabel`, `tag`, and `cards`.

### 5.3 What actually happened when I fed it our backlog

**Attempt 1 — 12 real ready records, real ids and titles.** Schema-valid, then rejected by the
**layout validator**, exit 1:

```text
Workflow layout validation failed:
- Label "basicly-vkh0.29" (~102px) is wider than node "nvkh0_29" (92px) — shorten the label or increase node.width.
- Sublabel "flip the tracker mode to owned and delet" needs ~144px at the 6px legible minimum,
  but node "nvkh0_29" provides 84px — shorten the sublabel or increase node.width.
```

Archify runs **text metrics** against box geometry and refuses anything illegible. An adapter
therefore has to own font measurement, or truncate our titles to fit — our record ids alone are
already too wide for a default node.

**Attempt 2 — all 168 ready records, 28 lanes × 6 columns.** The renderer did not merely refuse,
it **crashed**, exit 1:

```text
Renderer failed before emitting a structured diagnostic.
[internal/unclassified] Renderer failed before emitting a structured diagnostic.
```

**Attempt 3 — density bisection** (synthetic minimal nodes, 3 lanes, varying nodes per lane):

| Nodes per lane | Total | Result |
| --- | --- | --- |
| 1 | 3 | ok |
| 2 | 6 | ok |
| 3 | 9 | **fail** — `Nodes "n1" and "n2" are less than 8px apart in lane "l0"` |
| 4, 5, 6 | 12–18 | fail, same cause |

At default sizing the effective capacity is **2 nodes per lane**, not the 6 the `col` cap
suggests. The shipped 12-node example uses 6 lanes for 12 nodes — exactly that density.

**Attempt 4 — 168 nodes as 84 lanes × 2.** This *does* validate and render (exit 0). The result
is the honest answer to "can it scale":

| Artifact | SVG viewBox | File size |
| --- | --- | --- |
| archify's own 12-node example | `0 0 720 900` | 645 KB |
| our 168 ready records | **`0 0 720 10572`** | 811 KB |

A canvas **720 px wide and 10 572 px tall** — a ribbon 14.7× taller than it is wide, with 84
labelled swimlanes. It is a valid artifact and it is not a dashboard. And that is only the
**ready** subset; the full backlog is 934 records.

### 5.4 The one thing that does work: aggregate counts in `cards`

`cards` is a free-text sidebar (`schemas/common.schema.json`, `dot` ∈ 7 colours, `title`,
`items: [string]`). I generated one from live `tracker stats` and it validates and renders —
`rg` finds `records 934`, `ready 168`, `blocked 54` in the output HTML. So **aggregate
counts can be shown, as text, in a sidebar.** Per-record state cannot be shown at all. That is
the precise boundary of what archify can do with our work data.

## 6. What an adapter would have to do

Not impossible — but note what it is actually being asked to do, because the list is the
argument:

1. **Mangle ids.** Archify ids match `^[a-zA-Z][a-zA-Z0-9_-]*$` (`schemas/common.schema.json:8`).
   Our ids (`basicly-vkh0.42.2`) contain dots. The mapping must be injective and reversible, and
   `vkh0.42.2` → `vkh0_42_2` collides with a hypothetical `vkh0-42-2`.
2. **Invent a layout engine.** Assign every record a lane and a column ≤ 5, at ≤ 2 per lane,
   with no crossings. This is the graph-layout problem archify deliberately does not solve, and
   the adapter would own it forever.
3. **Own text metrics.** Truncate labels and sublabels to widths that pass a validator whose
   pass/fail depends on glyph advance widths.
4. **Discard status.** Encode it in `tag`/`sublabel` prose, or abuse the 7-value infrastructure
   enum as a status palette — which is exactly the semantic drift `PRODUCT.md:34` forbids.
5. **Cap the population.** Present a curated ~24-record subset, because the full set produces a
   10 572 px ribbon.

Steps 2, 3 and 5 mean the adapter would be *bigger and more fragile than a purpose-built
renderer*, and step 4 means it still would not answer the question. **An adapter is possible;
it is not worth building.**

## 7. Extensibility — the view set is fixed

Closed, by two independent mechanisms:

- `const TYPES = new Set(['architecture', 'workflow', 'sequence', 'dataflow', 'lifecycle']);`
  — `archify/bin/archify.mjs:13`. `rendererPath()` at `:42-47` maps a type straight to
  `renderers/<type>/render-<type>.mjs` and calls `fail()` on anything else. **There is no plugin
  registry, no renderer discovery, no extension hook** (searched `plugin|registry|DIAGRAM_TYPES`
  across `bin/`).
- Each schema pins `diagram_type` as a JSON-Schema `const`.

Within a diagram, the reader-facing extension point is `guidedViews`, capped at
**`maxItems: 5`** (`schemas/common.schema.json:62`), each with ≤ 48-char label and ≤ 140-char
note. Adding a genuinely new view means forking the renderer.

The **in-artifact interactivity is real and good**, but it is all topology: node finder, route
probe ("shortest authored directed path"), semantic lens, upstream/downstream reach,
presentation stage, export, guided views (enumerated from `aria-label` attributes in a rendered
artifact). None of it is stateful or data-bound.

## 8. Static or live — genuinely live, at file granularity

This is the one place my prior was wrong and worth recording. A first grep for
`watch|live|websocket` returned 52 hits that were **all the substring "swatch"** — a textbook
bad probe. Re-run with word boundaries plus a positive control (`addEventListener` must exist),
the real mechanism is:

- `archify preview` starts a local HTTP server, and unless `--no-watch`, calls
  `fs.watch(path.dirname(inputPath), ...)` filtered to the input file
  (`archify/bin/preview.mjs:589-593`), **plus** a `setInterval` poll as a fallback (`:599`).
- On change it hashes the source (`sourceDigest`), debounces, rebuilds, and pushes state to the
  browser over **Server-Sent Events**: `var events = new EventSource('/events')` with an
  `addEventListener('state', ...)` handler (`archify/bin/preview.mjs:128-131`).

So: **it re-renders on data change.** If an adapter regenerated the JSON on every ledger append,
`archify preview` would refresh the page. But this is *whole-artifact regeneration and reload*,
not incremental state binding — and it is a local dev-preview server, not a publishable
dashboard. The published `render` output is a static file.

## 9. What it would cost us

**The dependency is the largest line item, and it is not small.**

- Our **entire committed Node surface today is one package**: `markdownlint-cli2 ^0.23.0`
  (`/home/niksa/development/basicly/package.json:4-6`), 87 packages in `package-lock.json`.
  (`node_modules/` currently holds 148 directories including `@mermaid-js` and `cytoscape` — but
  `node_modules/` is gitignored (`.gitignore:53`) and those packages are **absent from the
  lockfile**. They are local leftovers, not a committed dependency. I nearly reported them as
  one; they are not.)
- Archify has **zero runtime dependencies**, which is the best possible version of this cost.
  But adopting it still means: a second Node tool in a Python/`uv` repo, `node >= 18` on every
  developer machine and in CI, and a vendored ~1 MB skill payload (`archify.zip` is 1.0 MB) or
  an `npx` network fetch inside the harness.
- Adding a dependency is on this repo's **explicit-confirmation list** (`CLAUDE.md`, "Adding /
  removing / upgrading dependencies").

**What we would own forever:** the adapter of §6 — an id mangler, a layout heuristic, a text-fitting
routine, and a curation rule for which ~24 of 934 records to show. Every archify schema change
(19 releases in 4 months) is a change our adapter must track, and its layout validator is strict
enough that a *cosmetic* upstream change to default node width would turn our generator red.

**What we would gain, honestly:** a very good static architecture picture, and a delta review
tool (§10).

## 10. The part that is genuinely good — and it is not the dashboard

Two features are better than anything we have, and both are about **architecture**, not work:

**Source-grounded components.** A component may carry up to 3 `sources` entries of
`{path, line, end_line, label}`, verified against a pinned revision with
`--repo-root`. This is not decorative — it caught a real error of mine. I pointed a component at
`src/basicly/tracker_query.py` at HEAD `ee7d263` and it refused:

```text
/components/0/sources/0/path does not identify a file at revision ee7d263...
[repository-evidence/file-missing]
```

I assumed the tool was wrong. `git cat-file -e ee7d263:src/basicly/tracker_query.py` →
`exists on disk, but not in ee7d263`. The file is staged-added and **uncommitted**; archify was
right and I was wrong. Re-pointed at a committed file, it validates clean. **Caveat:**
`meta.repository.url` must match `^https://github\.com/...` and `revision` must be a 40-hex SHA
(`schemas/architecture.schema.json`). `niksavis/basicly` is public on GitHub, so this is
available to us.

**`compare` — before/after architecture delta.** Ran it on two authored snapshots; exit 0 with a
machine-readable receipt carrying `rawSha256` + `semanticSha256` per side and a typed summary
(`components: {added, changed, evidenceChanged, removed, moved}`,
`connections: {added, changed, removed, rerouted}`). It requires stable authored ids on every
connection and says so precisely (`delta/relationship-id-required`, with
`supportedFixes`). This is a real capability with no equivalent in our toolchain.

Both, though, compare **authored diagrams**. Neither derives architecture from code. The
staleness problem stays ours.

## 11. Alternatives considered and rejected

**Mermaid — the incumbent, and it already won this decision.** `docs/architecture/conventions.md:101`
records: *"**Mermaid is the diagram language** [verified 2026-08-16]. It reads as text for a
coding agent, it renders on the hosting site, and it needs no build step. No other candidate
holds all three properties."* Archify fails two of the three — it needs a render step, and its
JSON IR with hand-placed pixel coordinates reads far worse to an agent than mermaid's text. That
same file already declines `C4Context`, `block-beta` and `architecture-beta` with reasons, and
`§6.1` names five diagram types that must **not** be added. Any archify adoption reopens a
dated, argued decision that is 1 day older than this review. It also notes mermaid's real
weakness — *"mermaid gives a flowchart no layout control"* (`:105`) — which is exactly what
archify fixes, at the price of the other two properties.

**A generated HTML/SVG view written in Python.** No Node, no adapter impedance, full control of
status encoding, and it can read `tracker ready/blocked/stats` directly. Rejected as an
*alternative* only in the sense that it is not off-the-shelf — it is in fact the recommendation
for the dashboard half, and it costs us a renderer we would own. Against archify's adapter (§6)
it is *less* code, because the layout heuristic and text-fitting are needed either way and here
they are not fighting a validator.

**Graph libraries with automatic layout (cytoscape.js, d3, vis-network).** These solve the one
thing archify refuses to do — place 168 nodes without a human — and they bind arbitrary data
fields to visual encodings, so status is trivial. Rejected for now because each is a browser
runtime dependency and a build step, i.e. it loses on the same two mermaid criteria, while
being a bigger commitment than archify. Worth revisiting if the dashboard becomes a product
surface rather than a repo view.

**Hosted dashboards (Grafana, Datasette).** Rejected: a service to run and secure, for a CLI
tool distributed as a Python package. `docs/architecture/conventions.md:133` already makes the
neighbouring argument — there is no deployed topology here.

**Doing nothing new.** This repo's accepted direction for status is already decided and it is
**not a diagram**: bead `basicly-e2mz.37` records *"Owner decision 2026-08-17: D-30 is accepted.
One source for the capability status view, rendered into all three surfaces by a docs-claims
generated block."* A generated table from one source, gated by `.scripts/docs_claims.py`. For
*status*, that decision is one day old and archify does not serve it.

## 12. Claim table

| Claim | Verdict | Rung | Source | Verified at |
| --- | --- | --- | --- | --- |
| Licence is MIT and permits our use | CONFIRMED | 2 (artifact) | `LICENSE:1`, `archify/package.json:7` | v2.15.0 `e1ac748`, 2026-08-17 |
| Licence carries a second, upstream copyright | CONFIRMED | 2 | `LICENSE:3-4` | 2026-08-17 |
| Actively maintained | CONFIRMED | 2/5 | `git log -1`; GitHub API `pushed_at` | 2026-08-17 |
| Effectively single-maintainer, 4 months old | CONFIRMED | 5 (API) | `contributors` = 139/2/2/2/1/1/1; `created_at` 2026-04-15 | 2026-08-17 |
| Zero runtime dependencies | CONFIRMED | 2 | `archify/package.json:27-30` (devDeps only) | v2.15.0, 2026-08-17 |
| Output is a self-contained single file | **REFUTED (partly)** | 2 (rendered artifact) | fetches `fonts.googleapis.com` webfont; local fallback exists | v2.15.0, 2026-08-17 |
| Schema can carry a work-item status | **REFUTED** | 2 (schemas) | `additionalProperties: false`; `componentType` 7-value infra enum | v2.15.0, 2026-08-17 |
| Can render our 168-record ready set usefully | **REFUTED** | 2 (ran it) | crashes at 28×6; at 84×2 yields `viewBox 0 0 720 10572` | 2026-08-17 |
| Effective density is 6 nodes/lane (the `col` cap) | **REFUTED** | 2 (bisection) | fails at 3/lane: `less than 8px apart` | 2026-08-17 |
| Aggregate counts can be displayed | CONFIRMED | 2 (rendered) | `cards` with live `tracker stats`, exit 0 | 2026-08-17 |
| View/diagram set is extensible by a consumer | **REFUTED** | 2 (source) | `bin/archify.mjs:13,42-47`; no plugin registry | v2.15.0, 2026-08-17 |
| Artifact is static only | **REFUTED** | 2 (source) | `fs.watch` + SSE, `bin/preview.mjs:589-593,128-131` | v2.15.0, 2026-08-17 |
| Source grounding is real and strict | CONFIRMED | 2 (tripped it) | rejected an uncommitted path at a pinned SHA; git confirmed | 2026-08-17 |
| `compare` produces a typed delta receipt | CONFIRMED | 2 (ran it) | exit 0, semantic SHAs + typed summary | v2.15.0, 2026-08-17 |
| Vendor targets dashboards | **REFUTED** | 3 (vendor doc) | `PRODUCT.md:24` anti-reference | 2026-08-17 |
| We already carry mermaid as a committed dep | **REFUTED** | 1 (our repo) | `package.json:4-6` = markdownlint-cli2 only; extras absent from lockfile, `node_modules/` gitignored | 2026-08-17 |

## 13. Could not establish

- **The upstream Cocoon AI "architecture-diagram-generator" repository.** `LICENSE:3-4` names it
  but gives no URL, and I did not search for it. Its licence terms are asserted by archify's
  own LICENSE and not independently verified. Settled by: locating the upstream repo and reading
  its LICENSE. Matters only if we vendor rather than depend.
- **Whether the 168-node renderer crash is a bug or a guard.** The message
  `[internal/unclassified] Renderer failed before emitting a structured diagnostic` is by its own
  wording an unhandled path. I did not bisect the crash or check the issue tracker for it.
  Settled by: bisecting node count at 6-per-lane, then searching `tt-a1i/archify` issues.
- **Whether `visual-check` would pass on the 10 572 px artifact.** `visual-check` needs a
  browser; I did not run it. Settled by: `node bin/archify.mjs visual-check <output.html>`
  with a headless Chrome available.
- **Real-world adapter performance / render time** at our scale. Not measured; all probes were
  sub-second but none rendered the full 934-record set.
- **Whether archify's SSE preview survives rapid successive writes** (our ledger is
  append-only and can burst under multi-lane fan-out). The debounce at `preview.mjs:579` suggests
  it is handled; not tested.
- **Contributor count excluding bots/merge attribution** — the API `contributors` endpoint was
  taken at face value.

## 14. Contradicts a committed claim

Nothing in this repository is refuted by these findings. Two things are worth naming so they are
not later mis-cited:

- `docs/architecture/conventions.md:101` ("Mermaid is the diagram language … No other candidate
  holds all three properties", verified 2026-08-16) **stands**. Archify was tested against those
  three properties and fails two. If archify is ever adopted for §10's delta review, that line
  needs an explicit carve-out amendment in the same commit — not a silent second tool.
- `README.md:17` of **archify** ("One file, ready to trust and share … self-contained HTML")
  overstates by one webfont fetch. That is the vendor's document, not ours; recorded here so a
  future reader does not repeat the claim on our surfaces.

One incidental observation, not a defect: `src/basicly/tracker_query.py` is staged-added and not
yet committed at `ee7d263`, which is the expected state of an in-progress tracker-ownership
change occupying the working tree at review time. It is recorded only because archify's source
verifier is what surfaced it, and because I initially assumed the tool was wrong. Left untouched.

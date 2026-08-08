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

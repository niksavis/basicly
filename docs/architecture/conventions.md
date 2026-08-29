# Conventions for the architecture document

**This file holds everything about how [`architecture.md`](architecture.md) is produced.**
None of it belongs in that document. An architecture document describes a system: its
parts, its boundaries, the contracts across them, its invariants, the decisions that shaped
it, its quality attributes and what it refuses to be. A reader who wants to know how the
system works must not have to read about the file's toolchain first.

**The test that decides where a passage lives.** If it would still be true and useful with
the architecture document rewritten in a completely different format, it is architecture. If
it would become meaningless, it is production, and it belongs here.

**Why a sibling file and not `CONTRIBUTING.md`.** `CONTRIBUTING.md` is a contributor's
runbook for the whole repository. A diagram-renderer comparison and a Diátaxis quadrant table
would bury the parts of it a contributor actually needs. This file sits next to the document
it governs, so a reader who opens `docs/architecture/` finds both.

---

## 1. Audience and viewpoints

The architecture document serves three readers: someone implementing a change to the engine,
someone debugging a live run, and someone reviewing whether a proposed change is allowed. It
serves them through five viewpoints, and every section belongs to exactly one part.

| Viewpoint | The question it answers | Part |
| --- | --- | --- |
| Context | What is outside this system, and what does it promise us? | I |
| Constraint | What may a change never break, and what does the system trade? | II |
| Functional | What does each component do, and what are its invariants? | III, IV |
| Information | What is the durable state, and who may write it? | V |
| Development and deployment | How is the code layered, and what runs where? | VI |

Decisions live in Part VII, not in the functional sections. A functional section states a
rule. A decision record holds the argument for it, the alternative rejected, and the
consequence accepted. Where the reasoning *is* the mechanism, it stays in the functional
section and the record points up at it.

## 2. The survival rule

**The architecture document must stand alone if the code and every other document
disappears.** It must be enough to rebuild the system from scratch. A decision and its reason
stay. An invariant, a constraint and a data shape stay. Everything else is a candidate for
deletion.

That rule is why the decision records are a section of the document rather than a
`docs/adr/NNNN-*.md` tree. The ADR *shape* is adopted — a stable id, a status, the context,
the decision, the consequences, and a supersession line. The ADR *directory* is declined,
because it fragments a document whose survival rule requires it to be one file. The shape is
right, so a later conversion to files is mechanical.

## 3. Authority order, when two sources disagree

1. **The code wins.** It is the only thing that runs. Where a number is cheap to re-derive,
   the document gives the command instead of the number. A copied figure goes stale in
   silence.
2. **The architecture document wins over every other document.** That covers the requirements
   documents, the implementation plan, the README, the landing page, the tutorial and the
   how-to pages.
3. **A claim about an external interface is never settled from recall.** That covers a
   command-line flag, a model id and a vendor limit. Read this repository's own adapter
   first. Then fetch the vendor's live documentation.

**One exception, and it is the reason this document set exists in its current form.** When a
rewrite is commissioned to specify a target rather than report the tree, the authority
inverts for that pass: the document specifies and the code is changed to match. A passage
written under an inverted authority carries a `[TARGET]` marker so no reader mistakes a
specification for a report.

## 4. Measured figures

**Every measured figure carries the date, and the command that re-derives it where one
exists.** `[measured 2026-08-16, <command>]` is the form. A bracketed date with no command is
allowed only where re-derivation is genuinely unavailable, and the sentence then says why.

**A number in a claim is derived twice, by paths sharing no step.** A disagreement between
two derivations is an instrument fault until proved otherwise.

**An empty probe is not evidence of absence.** A search returning nothing is ambiguous
between "absent" and "wrong probe". Run a positive control that must return something before
recording a zero. Where the document records an absence, it carries its positive control
inline.

## 5. Section numbering

Section numbers in the architecture document are a **contract with the code**, and the
document itself states that contract because it binds the code. The authoring rules that keep
the contract holdable are here.

1. **A number is stable.** It names one subject for as long as that subject exists.
2. **Inserting a section appends to the end of its part, or takes a decimal under an existing
   number.** It never renumbers a sibling.
3. **A renumber is a code change.** If a number must move, every citation moves in the same
   commit. A citation resolving to the wrong section reads as correct and is worse than a
   dangling one.
4. **Anchors are heading-text slugs**, so a heading rename moves every link to it. Check
   before committing.

## 6. Diagram convention

**Mermaid is the diagram language** [verified 2026-08-16]. It reads as text for a coding
agent, it renders on the hosting site, and it needs no build step. No other candidate holds
all three properties.

**Each diagram stays small**, because mermaid gives a flowchart no layout control.

**Every `classDef` sets an explicit text colour**, because the theme otherwise follows the
reader's colour mode and a node becomes unreadable in one of the two.

**Only three diagram types are used**: `flowchart`, `sequenceDiagram` and `stateDiagram-v2`.
All three are long-stable.

| Declined | Why |
| --- | --- |
| `C4Context` and the other C4 types | mermaid's own documentation calls them experimental, and the syntax may change. A context view is expressible as a plain `flowchart` |
| `block-beta` | beta, by its own name |
| `architecture-beta` | beta, and aimed at cloud services and groups this project does not have |

**Never name a participant or a state with a mermaid keyword.** One revision named a
`sequenceDiagram` participant `Loop`, which collides with mermaid's `loop` keyword. A parser
caught it; review did not.

**No gate parses these blocks.** `backlog.md` B3 is the item that would close that, and it
needs a dependency addition, so it is a human decision.

### 6.1 Five diagram types have nothing to show here

This is a fact about the documentation, not about the system. A reviewer who reports any of
these as a missing view is wrong. **Do not add one.**

| Type | Why it is not used |
| --- | --- |
| System / network diagram | There is no deployed topology. No region, no availability zone, no VPC. `basicly` is a CLI distributed as a Python package |
| Entity-relationship diagram | There is no application-owned database. The one SQLite file in the tree belongs to an external binary. Persistent state is JSONL and YAML, and a schema table is the honest tool |
| Class diagram | `rg -c '^class ' src/basicly` finds 200 classes, of which 158 carry `@dataclass`, against 1361 top-level functions. The only inheritance is exception hierarchies and `Protocol` declarations. There is no hierarchy to draw |
| Object diagram | An object diagram shows *instances* at one moment, not classes. A CLI process starts, works and exits. There is no long-lived object graph |
| "UML diagram" as a peer type | UML is a family, not a diagram type. The sequence and state diagrams in use already are UML |

### 6.2 The node legend stays in the architecture document

A reader needs the legend to read the diagram in front of them, so the small legend block
lives in the architecture document beside the component-state vocabulary it renders. The
argument for mermaid, the vendor comparison and the declines above stay here.

## 7. Which gates bind on which document

**Two `docs_claims` assertions bind on the architecture document** [verified 2026-08-16,
`.scripts/docs_claims.py`, the `ASSERTIONS` tuple].
`uv run python .scripts/docs_claims.py --check` reports `4 generated blocks current,
5 assertions current`; the other three assertions bind on other files.

1. `cli-commands` — every subcommand the CLI ships must appear in the CLI section's tables.
2. `cli-subcommands` — every subcommand of a *group* must appear in that group's own rows.
   This assertion exists because a single group row satisfies the first one. That is how
   several worktree subcommands stayed undocumented while every gate passed.

**One generated block binds on it**: the always-on size table, between paired
`docs-claims` markers. Never hand-edit inside the markers.

**Four pytest tripwires bind on it**, in `tests/test_docs_drift.py`. Two cover the CLI
section and two cover the fragment field table. Editing either section can turn the suite
red.

1. every registered subcommand appears in the CLI section;
2. the CLI section documents no unregistered subcommand — the reverse direction the
   `cli-commands` assertion cannot check;
3. the fragment table's `category` row equals `schema.CATEGORIES`;
4. every field the fragment table names is a real `Fragment` field.

**Tripwires 3 and 4 are the reason the fragment field table exists at all.** Deleting it in
favour of a citation to the schema file would blind both. `schema_version` is a source-file
key with no dataclass field, so the document states it in prose rather than as a table row.

**The citation ratchet binds on nothing in it**, because the document prefers a symbol name
or a command to a line number everywhere. That preference is deliberate: a line number goes
stale on an unrelated edit, and a symbol name does not.

**Three literals locate a section by heading text**, and a heading rename must move all
three in the same commit.

| File | Constant |
| --- | --- |
| `.scripts/docs_claims.py`, in `_cli_section` | the CLI section heading |
| `tests/test_docs_drift.py` | `CLI_SECTION` |
| `tests/test_docs_drift.py` | `FRAGMENT_SECTION` |

## 8. The documentation set

The architecture document is the **reference** quadrant and nothing else. A reference answers
"what is it, and how is it specified". It cannot also take a new consumer from install to a
first shipped unit. An attempt to make it both is what left that path missing.

| Quadrant | Where | Job | Written for |
| --- | --- | --- | --- |
| Tutorial | `docs/tutorial/` | one guaranteed-success path, install to shipped unit, no options offered | a consumer on day one |
| How-to | `docs/how-to/` | the recurring operations, one page per task | a consumer with a job to do |
| Reference | `architecture.md`, plus `CONTRIBUTING.md` | the system as specified | anyone implementing or debugging |
| Explanation | the decision records, `architecture.md` §38 | why one question was settled the way it was, with the measurement and its date; a research document is absorbed into a record and deleted, its last commit cited | anyone changing a decision |
| Order | the work tracker: `basicly session start`, `basicly tracker ready` | which records get built next, and why in that order | whoever is planning the next release |

**Four companion files sit beside the architecture document, and none is reference
material.**

| File | What it holds | Why it is not in the document |
| --- | --- | --- |
| [`status.md`](status.md) | the capability status view | a status row changes on every landing. A specification must not go stale on a schedule it does not control |
| [`backlog.md`](backlog.md) | defects found while writing the document | their real home is the tracker. The file is a holding pen, and each entry names the bead that should replace it |
| this file | how the document is produced | it describes the document, not the system |
| the rewrite changelog, if one is in flight | what a rewrite pass changed | it describes an edit, not the system |

**Three rules keep the layer from decaying into a second, competing account of the system.**

1. **Someone runs a tutorial command before they write it.** Every command and every quoted
   output in the tutorial ran against a fresh repository. A walkthrough is the one surface
   where an untested step costs the reader the whole session. The reader has no model yet, so
   they cannot notice the step is wrong.
2. **A how-to states the operation and its failure text, not the design.** Where it needs a
   reason, it links to the architecture document. A duplicated rationale goes stale first.
3. **Where any of them disagrees with the architecture document, that document wins.** The
   tutorial, the how-to pages, the README and the landing page are consumer-facing
   renderings, not independent sources. The requirements documents are the arguments behind a
   decision recorded in the architecture document, and each one is archived once absorbed.

## 9. Writing principles

Four rules, and none of them is a house style preference.

1. **Short sentences.** One clause carries one fact.
2. **One topic per sentence.**
3. **Active voice.** Name the actor. "The engine refuses" beats "the advance is refused".
4. **One word, one meaning.** The architecture document's glossary is the closed vocabulary,
   and it names each retired alias.

**A name for a new concept is checked against the code before it is adopted, not after.** A
word already bound to a referent in the kit is **unavailable**, however well it reads. Search
for it first; a proposal that survives the search is a candidate, and one that does not is a
collision the glossary would have to carry forever. `record` was proposed for an event kind
and is unavailable, because it already names the work item on every event, in the fold's output
map, in `snapshot.record_to_dict` and in what `basicly tracker shadow` counts. D-34 records
that rejection, and it is the worked example of this rule.

**Verdict first, evidence marked, no filler.** A paragraph opens with the claim in bold and
then supports it. A measurement carries its bracket. A limit is stated rather than covered by
an implied guarantee.

**No conformance claim is made to any controlled-language standard, and none may be added.**
The three portable principles above are ones ASD's publisher endorses for general use.
ASD-STE100 itself — Simplified Technical English, Issue 9, ASD, Brussels, 15 January 2025 —
states in its publisher's own FAQ that it is **not** intended for general-purpose writing,
and its 53 rules and controlled dictionary are not applied here. Claiming conformance would
overclaim against a specification nobody here has read.

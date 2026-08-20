# Config fragments

One file per lane. A lane that would otherwise append to a shared landing anchor
writes its contribution here instead of editing the anchor:

```text
basicly.d/<bead-id>.toml
```

Two things belong in it, and nothing else has to: a verify check the lane wires,
and the ratchet numbers the lane's own change moves.

```toml
# The gate this lane added, exactly as `[[verify.checks]]` in basicly.toml spells it.
[[verify.checks]]
name = "my-gate"
command = ["uv", "run", "python", ".scripts/my_gate.py"]
modes = ["fast", "full"]

# What this lane changed about a ratchet's recorded state. Never a new total.
[ratchet.noqa_debt]
count_delta = 1

[ratchet.noqa_debt.frozen]
S603 = 1
```

## Why the name

`d` is **directory**, the Unix drop-in convention — `init.d`, `cron.d`, `conf.d`.
`<name>.d` means "a directory of fragments that compose into `<name>`", so the name
says both things: `basicly.toml` is the assembled config, and you add a file here
rather than editing it. `changelog.d/` is the same convention for `CHANGELOG.md`.

## Why a file per lane

The filename carries the bead id, so it is unique by construction and two lanes
cannot write the same file. Three of five lanes bounced on the merge queue on
2026-08-08 (`basicly-u2hl:bc7cc925`), and every bounce was a shared anchor no bead
declared: `[[verify.checks]]`, which `.12`, `.14` and `.15` each appended an entry
to, and `pyproject.toml`'s ratchet tables. Serializing the lanes would detect the
collision; a file per lane removes it (`basicly-ef7t`, applying `basicly-4746`).

## Why every ratchet number is a delta

Because a total does not compose. Two lanes each adding one `S603` suppression both
measure the tree-wide count as 16, so both record 16 — and the merged tree holds 17
and fails a gate neither lane's rebase conflicted on. `count_delta = 1` from each
sums to the tree that landed, and addition is commutative, so the composed baseline
does not depend on landing order.

`count_delta` moves the table's tree-wide total (`waiver_count` for `module_size` and
for `comment_density`, `unreasoned_count` for `noqa_debt`).
`[ratchet.<gate>.frozen]` moves one recorded entry each. An entry whose deltas reach
zero is dropped, which is the rule the ratchet tables already state for a debt that
has been paid off.

## `frozen` may only move the safe way, and one gate has no such way

`module_size` and `comment_density` bound a subject, so a `frozen` delta that **raises**
a recorded baseline — or that names an entry `pyproject.toml` does not — is refused.
`ratchet.py` says the list is closed and that an added entry is a line a reviewer sees;
before `basicly-e2mz.20` a fragment could do both, and one had.

`noqa_debt` is different: its record must *equal* the tree, and the gate fails on "up
from the frozen" and "down from the frozen" alike. A `+1` there keeps the record true
rather than loosening it, so that gate declares `may_only = "track"` and takes either
direction. The direction belongs to the gate because only the gate knows what its
subject means.

## `rebaselined`, for the one case `frozen` cannot carry

A baseline sometimes has to rise with no narration added: deleting code that was less
prose-dense than its module raises the module's *share* while both prose and code fall.
That case declares itself, with a reason, and is counted on the gate's pass line:

```toml
[ratchet.comment_density]
rebaseline_reason = "code deletion shrank the denominator: prose fell 503 tokens, code fell 985"

[ratchet.comment_density.rebaselined]
"src/basicly/supervise.py" = 0.7
```

Its own table rather than a flag on `frozen` because the point is that it is countable —
`(74 frozen, 3 waived, 1 rebaselined)` — so a rebaseline cannot accumulate the way a
`frozen` delta silently did. A missing `rebaseline_reason` is refused.

## `base_commit`, for the measurement a delta was sized against

A delta composes in any order. The **headroom** you measured before choosing that
delta does not. Two lanes branched from one commit each measured `merge.py` at
exactly 2 tokens of headroom, each spent that same 2, and the composed tree failed a
gate neither branch failed (`basicly-nwx4ku`).

Record the commit you measured on, once per fragment, and a gate refuses the fragment
when `HEAD` does not contain it:

```toml
[ratchet]
base_commit = "be56ce2d0927d66c8b9168f69ab41457147b7641"
```

**Ancestry, not equality.** Work landing on top of your measurement does not stale it;
only a base this head does not contain does, which is a measurement taken on a tree
that is not this one.

**It is optional, and hand-written.** Nothing writes a fragment for you, so there is
no write-time hook to derive it at, and a fragment that records no base composes
exactly as it did before — absence is not a violation, or every fragment already in
this directory would stop landing. Recording it is a lane volunteering precision
about its own numbers. Git's third answer is not a violation either: where the
history is not there to read — a tree copied without its `.git`, a shallow clone —
the check has nothing to say and says nothing.

## The two ratchets do not share a denominator

`module_size` counts `module_tokens`, which **excludes top-level imports**;
`comment_density`'s share is over `_text_tokens` for the whole file. Sizing a cut with
the wrong one flips a marginal case, and nothing else in the repo says so.

## Why one gate's deltas are fractional

Two of the three ratchets count things — tokens, suppressions — so their deltas are
integers. `comment_density` records a **percentage share** to one decimal, so its
per-entry deltas are floats:

```toml
# The prose share this lane cut off a frozen module, and the waiver it took.
[ratchet.comment_density]
count_delta = 1

[ratchet.comment_density.frozen]
"src/basicly/thing.py" = -1.4
```

`dropin.compose` is told that by a `fractional=True` argument rather than reading it
off the values it was handed, for two reasons. A recorded table can be empty, and an
empty table offers nothing to infer from; and inferring from the values would admit a
float into a counting ratchet the first time one arrived, which is exactly the silent
widening the fragment schema otherwise fails closed on. `count_delta` counts entries,
so it stays whole for all three gates including this one (`basicly-05g0`).

## What still edits the anchor itself

Deleting a `[tool.module_size.frozen]` entry that has graduated, and any change to
the tables' hand-written argument. Both are rare and neither is what a lane does on
its way past; the fragments cover the appending, which is what collided.

## What reads them

`basicly.config` assembles `[[verify.checks]]` from `basicly.toml` and then every
fragment in filename order, and validates each fragment against the same schema as
`basicly.toml` — an unknown key here is refused, not ignored. `basicly.dropin`
composes the ratchet deltas, and all three ratchet gates under `.scripts/` read their
baseline through it. The pre-commit hook runner reads the same set, so a check
declared here runs in the hook as well as in `basicly verify`.

Fragments are not folded back into `basicly.toml`: unlike a changelog entry, a check
is permanent config, and a composed baseline is order-independent so it never needs
flattening to stay correct. This `README.md` is not a fragment; only `*.toml` is read.

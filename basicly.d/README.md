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

`count_delta` moves the table's tree-wide total (`waiver_count` for `module_size`,
`unreasoned_count` for `noqa_debt`). `[ratchet.<gate>.frozen]` moves one recorded
entry each. An entry whose deltas reach zero is dropped, which is the rule the
ratchet tables already state for a debt that has been paid off.

## What still edits the anchor itself

Deleting a `[tool.module_size.frozen]` entry that has graduated, and any change to
the tables' hand-written argument. Both are rare and neither is what a lane does on
its way past; the fragments cover the appending, which is what collided.

## What reads them

`basicly.config` assembles `[[verify.checks]]` from `basicly.toml` and then every
fragment in filename order, and validates each fragment against the same schema as
`basicly.toml` — an unknown key here is refused, not ignored. `basicly.dropin`
composes the ratchet deltas, and the two gates under `.scripts/` read them through
it. The pre-commit hook runner reads the same set, so a check declared here runs in
the hook as well as in `basicly verify`.

Fragments are not folded back into `basicly.toml`: unlike a changelog entry, a check
is permanent config, and a composed baseline is order-independent so it never needs
flattening to stay correct. This `README.md` is not a fragment; only `*.toml` is read.

# The foreign board producer

A second `harness-board/v1` producer that **imports no basicly module**, and the fake external
tracker export it reads.

| File | What it is |
| --- | --- |
| `export.json` | A fake export from `acme-tracker`, in that tracker's own vocabulary — `key`, `summary`, `state`, `severity`, `kind`, and no dependency edges |
| `produce.py` | The producer. Standard library only; maps the export into the contract |
| `snapshot.json` | What `produce.py` emits from `export.json`, checked in so consumers have a foreign corpus to render |

## Why it exists

`basicly-rn0o.13`: a conformance kit that is only ever run against the native producer cannot
detect cross-producer parity rot. `src/basicly/board_snapshot.py` is the reference producer,
not *the* producer, and a contract with one implementation is a wire format with an aspiration
attached. This is the cheapest honest second implementation — and `tests/test_board_parity.py`
runs the conformance check against both.

The same shape has already cost this project once, measurably: three agent families are
advertised and the dispatch ledger holds records for two. A capability nothing exercises reads
as working.

## What it costs to keep — stated, not hidden

**This is a maintained artifact, not a fixture.** The ongoing cost is real and it is the price
of the detection:

- **Every schema change reaches it.** A new required key, or a tightened constraint on a
  section `produce.py` emits, breaks it. That is the gate firing correctly — it is exactly the
  signal a single-producer kit cannot give — but it is a second edit in the same change.
- **It is a second implementation of the mapping**, so `board_snapshot.py` and `produce.py` can
  drift on purpose as well as by accident. `tests/test_board_parity.py` `DECLARED_ASYMMETRY` is
  where "on purpose" gets written down, with a reason, and an undeclared gap fails.
- **It is held to the kit contract, not `src/`'s.** No syntax newer than Python 3.9 and one
  exception class per handler, which *inverts* the paren-free `except A, B:` form
  `python-guidelines` prescribes everywhere else in this tree. A contributor moving code
  between the two has to switch styles.
- **`snapshot.json` is generated and checked in**, so it can go stale. `produce.py` stamps
  `generated_at` from the export's own `exported_at` rather than from a clock, which is what
  lets the parity suite assert the two are byte-identical instead of merely both valid.

The cheaper alternative — a hand-written foreign fixture and no producer — was not taken,
because a fixture cannot rot in the direction that matters. Only a producer can stop agreeing.

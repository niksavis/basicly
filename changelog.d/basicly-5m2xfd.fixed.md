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

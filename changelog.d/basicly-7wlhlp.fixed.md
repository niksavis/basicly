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

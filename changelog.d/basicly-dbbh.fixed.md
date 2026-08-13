A repair dispatch is now refused when the grant cannot pay for it. D3's halt predicate had three
enforcing call sites — delegated approval, supervised lane admission and decider delegation — and
`basicly-1th1` added a fourth for the interactive build dispatch, but the repair path reached
`runner.run` past all of them. So a landing that failed a gate briefed and spawned a metered agent
on an exhausted grant, which is exactly when a grant is most likely to be spent. The spend ceiling
is now checked before the repair spawns, and the brief is written back on refusal rather than
consumed, so "no budget" does not turn into "the failure is forgotten". D3's halt was split out of
the composite refusal for this, because a repair is a second attempt at work already planned and
already sized and must not be re-admitted against the plan gate or the working-set band.

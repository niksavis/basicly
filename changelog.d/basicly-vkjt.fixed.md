Answering `park` on a stalled lane now parks it. Five question shapes across
`policy.rework_escalation_question` and `supervise._capped_dispatch` offer that route, but the
carrier accepted the answer only from a decision of kind `escalation` — so an operator who parked
a stalled lane saw `answered <id> by human`, the bead stayed `open` and dispatchable, and the next
supervised pass ran it again. The carrier now binds on the `or park?` suffix every one of those
questions ends with, so it cannot accept a route its producer offers and then drop it. Answering
`park` on a question that offers no routes still holds nothing, and a delegated answer still
cannot park a lane.

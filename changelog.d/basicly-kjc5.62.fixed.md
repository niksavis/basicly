- **`basicly loop supervise` seeds lanes from a root whose decompose checkpoint a live grant
  delegates, instead of refusing and sending the operator back to `loop run` per child.**
  A root with children derives `decompose` (`loop_state.derive_phase`), and its decompose
  checkpoint gates the fan-out — so `loop supervise <epic>` answered `seed-blocked - no lane
  could be provisioned from 12 open child(ren) - decompose checkpoint awaiting human approval`
  and exited non-zero, under a live L3 grant that `policy.GRANT_COVERAGE` delegates exactly
  that checkpoint to. The cause was the driver: seeding used `loop.run_until_blocked`, which
  stops dead at a checkpoint and never reaches `policy.approve_checkpoint_guarded`, so no
  grant was consulted at any point on the seeding path. The operator then hand-drove
  `loop run` once per child, on the same root and the same grant, and every one delegated.

  Seeding now drives `loop.run_ceremony`, the same command `basicly loop run` is built on,
  naming the session's own root as the grant root. **Nothing is widened by the swap**: the
  ceremony's only route to an approval is that same guarded predicate, so a checkpoint no
  grant covers still stops the pass — and it now says which of the three things happened.
  A refusal names the level that *would* delegate the checkpoint and the command to issue
  one; a covering grant that declined repeats its own reason; a rejected confirm code reads
  as a refusal. "Awaiting human approval" said none of those, which is why an operator
  holding a covering grant could not tell it had never been asked.

  `basicly loop preflight` stops calling that checkpoint a blocker when the live grant
  delegates it, and prints the delegate-it remedy beside the approve-it one when nothing
  does. Preflight refusing a pass that now runs would be the same defect inverted
  (basicly-kjc5.62).

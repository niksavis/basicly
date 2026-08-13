VALIDATE now dispatches the `validator` role. The dispatch resolves its persona through
`roles.resolve_role` exactly as the repair dispatch does, falls back to the default runner when
the family cannot load the role rather than emitting a flag the host would drop, and runs in the
base checkout because a consumer exercises the merged product rather than the branch that made
it. It is metered like any other dispatch, so it binds the spend ceiling as a fifth site. When it
returns, the engine re-reads the gate instead of assuming a verdict was recorded — a dispatch
that recorded nothing leaves the unit resting in `validate`.

A validate dispatch is now recorded under `run_record.VALIDATE_PHASE` rather than `BUILD_PHASE`.
Every dispatch through `loop._run_agent` was previously labelled a build, which would have put a
read-only judge's cost into the write-dispatch sample the spend calibration prices a lane from.

Two extractions the size and density ratchets forced, both real seams: `dispatch_brief` now holds
the prompts the loop dispatches with, and `landing_gate` holds the reading of an answered gate
escalation and what it authorises. `landing_gate` carries a stated `comment-density-waiver` — its
four functions are small and their docstrings are the incident history that makes them correct.

The verdict is recorded by the engine, not by the validator. `br gate report` requires
`--provider` and authenticates nothing, so an agent told to report its own gate would
either error and record nothing — leaving the unit in `validate` forever while believing
it had reported — or self-certify a required gate. The validator now ends its reply with
`VALIDATION: PASS` or `VALIDATION: FAIL` and the engine writes the result under its own
provider.

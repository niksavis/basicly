- **A supervised pass now dispatches every ready lane while the review queue has room.** The
  downstream-WIP bound charged a pass for its own admissions, so six ready lanes under a limit
  of 5 started 5 and refused one against a limit nothing stood at. `wip.admit` gates on work
  already downstream: below the limit all start, at it none does.

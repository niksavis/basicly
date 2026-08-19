- **`basicly tracker write -- <subcommand> ...` makes one hand-authored tracker write through
  the engine seam.** Editing the append-only event log by hand appends events nothing
  validated, to a store with no undo; spawning a tracker binary beside the engine has the same
  effect, and three records on this repository's own tracker arrived that way and were the whole
  of what its store comparison could not reconcile. The verb routes the argv down the path the
  engine's own writes take, so the read-only guard, the argv classification and the event
  translation all apply to a human's write, and the seam's refusals land **before** anything is
  recorded: an unresolvable tracker mode and an argv the translator cannot represent are both
  refused ahead of the write rather than after half of it. The `work-tracker` skill names the
  verb, which is what makes it reachable to a dispatched agent rather than merely present
  (basicly-vkh0.24).

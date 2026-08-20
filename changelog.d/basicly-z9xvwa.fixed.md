- **A dispatched lane is told a working directory inside its own worktree, instead of being
  left to pick a session-wide scratchpad two lanes share.** The dispatch brief already said
  the lane sits in a dedicated worktree, and said nothing about where to put a script or a
  measurement, so a lane used the session scratchpad — which is keyed by session and not by
  lane. Measured on 2026-08-20 during a nine-lane pass: a sibling overwrote a lane's
  measurement script between the write and the run, and the run **printed the sibling's files
  and numbers under the first lane's command**, with no error. Six lanes' copies of files sat
  in one shared backup directory, so a restore would have written another lane's content into
  the wrong worktree.

  The failure is silent substitution rather than loss, which is why no positive control on the
  measured corpus catches it: the corpus was never wrong, the script was. The brief now names
  `.basicly/usage/scratch` and the mechanism together, because a rule without its failure mode
  reads as tidiness. The path is relative, which is what carries the isolation — it resolves
  against the lane's own worktree, so two lanes handed the identical brief still write two
  directories, and it sits under the already self-ignored `.basicly/usage/` so nothing a lane
  scribbles can reach a commit. `needs_input.SENTINEL_FILE` was checked as a second site of the
  same shape and is already relative. The close-out guidance names cross-lane substitution
  beside the cleaning hazard, which landed separately (basicly-z9xvwa).

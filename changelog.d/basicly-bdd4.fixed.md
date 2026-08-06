- **A lane bounced by a landing conflict is re-dispatched with the conflict as its
  task, and an identical repeat escalates without spending the rework cap.** A
  bounced lane was already re-dispatched, but the bounce published nothing, so the
  supervisor assembled the prompt the agent had already satisfied — for work already
  committed on its branch. The agent changed nothing, the landing re-derived the
  identical conflict, and the second attempt escalated having learned nothing. This
  fired three times on 2026-08-05/06, and every one of those conflicts was resolvable
  by hand in about two minutes.

  The bounce now publishes a `coupling` found-info record naming the conflicting
  paths, which lanes landed over them, and both sides — the same channel the
  supervisor already used for a collision it *predicted*, which the collision it
  *observed* never got. It is written once the pass is over, not at the bounce, so no
  pass ordering reaches a durable record. There is still no merge-time resolution: the
  lane's own agent resolves on its own branch.

  A landing that then fails with the same cause on the same paths as that lane's
  previous one escalates to the decision queue immediately, and the attempt the loop
  charged for it is refunded — re-applying one branch to one anchor cannot converge,
  so the attempt could not have changed the outcome (`basicly-bdd4`).

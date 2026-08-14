- **Every agent source must declare a model tier, and `catalog lint` now refuses one that does not.**
  The tier vocabulary is `low`, `medium`, `high`, `maximum`. The rule it enforces is that a dispatch
  with no resolved tier is a defect rather than a default: an omitted tier inherits the spawning
  session's model, which is usually the most expensive one, so the routing rule defeats itself in
  silence.

  **A consumer inherits this.** An overlay agent under `.basicly-local/agents/` that declares no
  tier now fails `basicly catalog lint` with a message naming the file and the allowed values. The
  check walks every agent root, so core and overlay get the identical diagnostic — the asymmetry
  where a rule bound on core sources and not on overlay ones is the same one already closed for the
  tier vocabulary.

  Two shipped roles had no tier and now do. `code-reviewer` and `security-auditor` are both `high`,
  each argued against the tiers the other roles already declare rather than assigned: the
  hand-invoked review path must not be weaker than the engine path on the same diff, and a role with
  read-only tools has no external oracle to check its own inference, so its failure mode is a silent
  false negative.

  **What this does not do, stated because the gap is easy to misread.** The declaration is now
  mandatory and checkable. It is not yet effective: no spawn in this repository reads the tier, and
  `basicly-a3yi` is the open work that injects it into a projected surface. A declared tier reaches
  no model today (`basicly-plhx`).

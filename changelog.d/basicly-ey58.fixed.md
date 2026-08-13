A role's declared `skills:` now reach the agent that was dispatched for it. The field is
documented and typed, but it is honoured only when a definition is spawned as a subagent —
under `claude --agent <name> -p`, the shape the engine dispatches with, it does nothing
(probed twice on claude 2.1.231 with a positive control). Five of eleven projected roles
declare skills, so every one of them ran without its specialism.

The engine now reads the bodies a role declares and carries them in the dispatch prompt,
ahead of the task. Measured against the alternative before choosing it: the largest role's
skills are 3,261 tokens where a lane costs 8–11 million, so this is about 0.03% of a lane —
and unlike the vendor's own mechanism it reaches codex and copilot too, which matters for a
harness that advertises three families. A role declaring no skills gets a byte-identical
prompt. A declared skill with no readable body is named in the prompt rather than logged,
because the agent is what can act on it by loading the skill itself.

This also gives `catalog_lint`'s skill/role pairing a runtime effect it did not have before,
so the lint now enforces something real.

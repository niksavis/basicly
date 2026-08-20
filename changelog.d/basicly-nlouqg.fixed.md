- **A ratchet gate can be rebaselined by the route its own remedy prescribes.** `[ratchet]` in
  `CONFIG_SCHEMA` named three gates while five call `ratchet.compose_ratchet`, so a lane that
  followed `code-citations`' or `release-notes`' printed remedy and wrote
  `[ratchet.code_citations]` into its `basicly.d` fragment got `unknown section
  'code_citations' in [ratchet]` from every command that reads the config — 166 tests the
  moment one did. Both gates shipped green because nothing exercised their rebaseline route:
  the section they compose from and the section the schema accepts were declared in different
  files and agreed only by review.

  The two missing names are registered, and the agreement is now derived rather than reviewed.
  `test_ratchet_sections_register_every_gate_that_composes_one` parses every `.py` under
  `.scripts/` and `src/basicly/` for a `compose_ratchet` call, resolves each call's gate
  argument through the module-level constant the caller spells it as, and fails naming the gate,
  the file that composes it and the declaration `src/basicly/config.py` is missing — so a sixth
  gate cannot land without its section. The walk asserts it has found the three long-registered
  gates before it reports a difference, because a probe that found nothing would pass for free,
  and it excludes `tests/` so a suite's fixture gate name is not demanded of the schema
  (basicly-nlouqg).

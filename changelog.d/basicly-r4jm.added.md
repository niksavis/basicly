Five of the seven unbuilt handoff artifact kinds now have schemas: `classification`,
`change-shape`, `verification-evidence`, `validation-transcript` and `release-record`.
Their absence is why `validator`, `curator`, `retrospector` and `reviewer` were authored
and unreachable — a role with no schema has nothing a state can validate, so no state
dispatches it.

Each is strict in the same way the two existing schemas are: `additionalProperties: false`
at every object level, a `required` array naming every declared property, and
`schema_version` pinned. `classification` is asserted against a payload built from
`integrity.assign()` rather than a hand-written example, because a schema agreeing with an
example someone wrote for it proves nothing.

The requirements' artifact table said "Six schemas" while listing seven rows and omitting
`release-record` entirely — corrected, with the row added. `solution-design` is the one
remaining kind with no schema, and deliberately: D17 specifies it as markdown sections
rather than a JSON payload, so whether it belongs to this family is an open question
(`basicly-32qz`) rather than an omission.

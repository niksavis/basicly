"""The build entry predicate, and the ratchet that decides who it binds.

The whole design of this predicate is a population discriminator: it gates on the
`## Plan` *heading*, not on the fields under it, because a body carrying the heading was
written by the decomposer under this gate while a body without one predates it. A
predicate that refused every bead filed before it existed would not gate the work that
comes after — it would stop the harness. These assertions pin both halves, because a
ratchet that binds nobody and a ratchet that binds everybody both look like "no
failures" from the outside.
"""

from __future__ import annotations

from basicly.plan_entry import entry_verdict_for

PLANNED = """\
## Acceptance Criteria

- Given a planned lane when it enters build then the gate admits it - check: this test

## Scope

- `src/basicly/plan_entry.py`

## Plan

- integrity: `L2`
- budget: `50000`
- depends on: none
"""


def test_a_body_with_no_plan_heading_is_admitted_because_it_predates_the_gate() -> None:
    """The bead population D8 refuses to bulk-transform. Refusing it stops the harness."""
    verdict = entry_verdict_for("basicly-old", "## Context\n\nFiled before the gate existed.\n")
    assert verdict.admitted
    assert verdict.reason == ""


def test_a_body_carrying_the_heading_and_every_field_is_admitted() -> None:
    """The other half of the ratchet: written under the gate and complete, so it passes."""
    verdict = entry_verdict_for("basicly-new", PLANNED)
    assert verdict.admitted
    assert verdict.reason == ""


def test_a_body_carrying_the_heading_but_missing_a_field_is_refused() -> None:
    """Under the heading, silence is a defect rather than an ambiguity — so it is named."""
    without_integrity = PLANNED.replace("- integrity: `L2`\n", "")
    verdict = entry_verdict_for("basicly-new", without_integrity)
    assert not verdict.admitted
    assert "integrity" in verdict.reason
    assert "basicly-new" in verdict.reason


def test_the_refusal_names_every_missing_field_at_once() -> None:
    """One round trip per lane, not one per field — the reason is what the operator acts on."""
    bare = "## Plan\n\n- depends on: none\n"  # no acceptance, scope, integrity or budget
    verdict = entry_verdict_for("basicly-new", bare)
    assert not verdict.admitted
    assert "integrity" in verdict.reason
    assert "budget" in verdict.reason


def test_the_heading_ratchet_discriminates_rather_than_admitting_everything() -> None:
    """The control: the same missing field is admitted without the heading and refused with it.

    Without this pair, a predicate that admitted every body would pass every other
    assertion here — which is the fail-open a ratchet is most likely to become.
    """
    body = "- depends on: none\n"  # the same incomplete body, with and without the heading
    assert entry_verdict_for("basicly-x", body).admitted
    assert not entry_verdict_for("basicly-x", f"## Plan\n\n{body}").admitted

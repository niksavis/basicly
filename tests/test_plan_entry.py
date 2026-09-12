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
    verdict = entry_verdict_for("basicly-old", "## Context\n\nFiled before the gate existed.\n")
    assert verdict.admitted
    assert verdict.reason == ""


def test_a_body_carrying_the_heading_and_every_field_is_admitted() -> None:
    verdict = entry_verdict_for("basicly-new", PLANNED)
    assert verdict.admitted
    assert verdict.reason == ""


def test_a_body_carrying_the_heading_but_missing_a_field_is_refused() -> None:
    without_integrity = PLANNED.replace("- integrity: `L2`\n", "")
    verdict = entry_verdict_for("basicly-new", without_integrity)
    assert not verdict.admitted
    assert "integrity" in verdict.reason
    assert "basicly-new" in verdict.reason


def test_the_refusal_names_every_missing_field_at_once() -> None:
    bare = "## Plan\n\n- depends on: none\n"
    verdict = entry_verdict_for("basicly-new", bare)
    assert not verdict.admitted
    assert "integrity" in verdict.reason
    assert "budget" in verdict.reason


def test_the_heading_ratchet_discriminates_rather_than_admitting_everything() -> None:

    body = "- depends on: none\n"
    assert entry_verdict_for("basicly-x", body).admitted
    assert not entry_verdict_for("basicly-x", f"## Plan\n\n{body}").admitted

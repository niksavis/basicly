from __future__ import annotations

from basicly.plan_entry import entry_verdict_for
from basicly.plan_record import ACCEPTANCE_FIELD, ACCEPTANCE_HEADING


def _record(description: str, acceptance: str = "") -> dict[str, str]:
    return {"description": description, ACCEPTANCE_FIELD: acceptance}


ACCEPTANCE = "- Given a planned lane when it enters build then the gate admits it"

PLANNED = """\
## Scope

- `src/basicly/plan_entry.py`

## Plan

- integrity: `L2`
- budget: `50000`
- depends on: none
"""


def test_a_body_with_no_plan_heading_is_admitted_because_it_predates_the_gate() -> None:
    verdict = entry_verdict_for(
        "basicly-old", _record("## Context\n\nFiled before the gate existed.\n")
    )
    assert verdict.admitted
    assert verdict.reason == ""


def test_a_body_carrying_the_heading_and_every_field_is_admitted() -> None:
    verdict = entry_verdict_for("basicly-new", _record(PLANNED, ACCEPTANCE))
    assert verdict.admitted
    assert verdict.reason == ""


def test_a_body_carrying_the_heading_but_missing_a_field_is_refused() -> None:
    without_integrity = PLANNED.replace("- integrity: `L2`\n", "")
    verdict = entry_verdict_for("basicly-new", _record(without_integrity, ACCEPTANCE))
    assert not verdict.admitted
    assert "integrity" in verdict.reason
    assert "basicly-new" in verdict.reason


def test_the_refusal_names_every_missing_field_at_once() -> None:
    bare = "## Plan\n\n- depends on: none\n"
    verdict = entry_verdict_for("basicly-new", _record(bare))
    assert not verdict.admitted
    assert "integrity" in verdict.reason
    assert "budget" in verdict.reason


def test_the_heading_ratchet_discriminates_rather_than_admitting_everything() -> None:

    body = "- depends on: none\n"
    assert entry_verdict_for("basicly-x", _record(body)).admitted
    assert not entry_verdict_for("basicly-x", _record(f"## Plan\n\n{body}")).admitted


def test_an_open_record_holding_acceptance_only_under_the_heading_is_refused_by_field() -> None:

    legacy = f"{ACCEPTANCE_HEADING}\n\n{ACCEPTANCE}\n\n{PLANNED}"
    verdict = entry_verdict_for("basicly-legacy", _record(legacy))

    assert not verdict.admitted
    assert verdict.missing == (ACCEPTANCE_FIELD,)


def test_a_closed_record_still_reads_its_acceptance_from_the_heading() -> None:

    legacy = f"{ACCEPTANCE_HEADING}\n\n{ACCEPTANCE}\n\n{PLANNED}"
    record = {**_record(legacy), "status": "closed"}

    assert entry_verdict_for("basicly-legacy", record).admitted

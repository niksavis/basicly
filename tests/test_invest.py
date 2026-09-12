from __future__ import annotations

import pytest

from basicly import invest

_PATCHING = (
    "## Trigger\n\n"
    "When a vulnerability is published against a dependency we ship, I want to patch it "
    "in the same release train, so I can keep the shipped surface free of a known "
    "advisory.\n"
)

_USER_VOICE = (
    "## Trigger\n\n"
    "As a release engineer, I want the changelog assembled from the landed records, "
    "so that I do not hand-write a release note twice.\n"
)


def test_a_job_story_trigger_states_a_trigger_with_no_persona() -> None:
    assert invest.trigger_voice(_PATCHING) == "job"


def test_a_user_story_trigger_states_a_trigger_too() -> None:
    assert invest.trigger_voice(_USER_VOICE) == "user"


def test_a_record_may_carry_both_voices() -> None:
    assert invest.trigger_voice(f"{_PATCHING}\n{_USER_VOICE}") is not None


def test_prose_under_the_trigger_heading_states_no_trigger() -> None:

    body = "## Trigger\n\nThe board goes stale against its template and nobody notices.\n"
    assert invest.trigger_voice(body) is None


def test_the_scaffold_placeholder_states_no_trigger() -> None:

    assert invest.trigger_voice(invest.JOB_STORY_EXAMPLE) is None
    assert invest.trigger_voice(invest.USER_STORY_EXAMPLE) is None


def test_a_body_with_no_trigger_section_states_no_trigger() -> None:
    assert invest.trigger_voice("## Acceptance Criteria\n\n- given x then y\n") is None
    assert invest.trigger_voice("") is None


@pytest.mark.parametrize("work_type", ["bug", "chore", "task", "feature", "epic"])
def test_every_work_type_owes_a_trigger(work_type: str) -> None:

    assert invest.TRIGGER_HEADING in invest.required_conditions(work_type)


def test_a_bare_acceptance_heading_satisfies_nothing() -> None:
    missing = invest.missing_sections(
        {"description": "## Trigger\n\n" + _PATCHING + "\n## Acceptance Criteria\n"},
        (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING),
    )
    assert invest.ACCEPTANCE_HEADING in missing


def test_a_todo_acceptance_criterion_satisfies_nothing() -> None:
    body = f"{_PATCHING}\n## Acceptance Criteria\n\n- TODO: Given x when y then z\n"
    missing = invest.missing_sections(
        {"description": body}, (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING)
    )
    assert invest.ACCEPTANCE_HEADING in missing


def test_either_acceptance_carrier_satisfies_testable() -> None:

    required = (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING)
    in_body = {"description": f"{_PATCHING}\n## Acceptance Criteria\n\n- given x then y\n"}
    in_field = {"description": _PATCHING, "acceptance_criteria": "given x then y"}
    assert invest.missing_sections(in_body, required) == ()
    assert invest.missing_sections(in_field, required) == ()


def test_a_trigger_is_missing_by_its_own_name() -> None:
    record = {"description": "## Acceptance Criteria\n\n- given x then y\n"}
    missing = invest.missing_sections(record, (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING))
    assert missing == (invest.TRIGGER_HEADING,)


def test_the_remedy_names_both_voices_and_demands_neither() -> None:
    remedy = invest.trigger_remedy()
    assert invest.JOB_STORY_EXAMPLE in remedy
    assert invest.USER_STORY_EXAMPLE in remedy

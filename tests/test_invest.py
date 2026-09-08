"""Tests for the INVEST conditions a record must satisfy to be dispatched (basicly-q1ve1fn).

The gate's whole point is the two voices, so most of what is asserted here is that a
trigger stated *without a persona* is as good as one stated with it. Bill Wake's original
INVEST asks only that a story be "valuable to the customer" and names no role, so a
checker that demanded `As a ...` would be stricter than the criterion it claims to
enforce — and would teach an author to prepend a persona the work does not have.
"""

from __future__ import annotations

import pytest

from basicly import invest

# The owner's own example of a trigger with no person in it: the situation is that a
# vulnerability was published, and nobody wants the patch in the sense a persona wants a
# feature. If this case ever fails the gate, the gate is demanding a fabricated persona.
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
    """The owner's patching case: a situation triggers the work and no person appears."""
    assert invest.trigger_voice(_PATCHING) == "job"


def test_a_user_story_trigger_states_a_trigger_too() -> None:
    """The persona voice is accepted, never required."""
    assert invest.trigger_voice(_USER_VOICE) == "user"


def test_a_record_may_carry_both_voices() -> None:
    """Cohn: the two techniques are compatible, so carrying both is not an error."""
    assert invest.trigger_voice(f"{_PATCHING}\n{_USER_VOICE}") is not None


def test_prose_under_the_trigger_heading_states_no_trigger() -> None:
    """A heading filled with context is the state the record was opened against.

    223 of 306 active records carried such a heading when this was measured, and one
    carried a trigger. Accepting the heading alone would gate on what everything
    already has.
    """
    body = "## Trigger\n\nThe board goes stale against its template and nobody notices.\n"
    assert invest.trigger_voice(body) is None


def test_the_scaffold_placeholder_states_no_trigger() -> None:
    """An angle-bracket placeholder has the shape and none of the content.

    The scaffold has to *show* the shape, so the hint necessarily matches it. A shape
    match on the hint would let a body nobody filled in clear the gate, which is the
    same lenient edge a bare acceptance heading opened.
    """
    assert invest.trigger_voice(invest.JOB_STORY_EXAMPLE) is None
    assert invest.trigger_voice(invest.USER_STORY_EXAMPLE) is None


def test_a_body_with_no_trigger_section_states_no_trigger() -> None:
    """The absence reads as absence rather than raising."""
    assert invest.trigger_voice("## Acceptance Criteria\n\n- given x then y\n") is None
    assert invest.trigger_voice("") is None


@pytest.mark.parametrize("work_type", ["bug", "chore", "task", "feature", "epic"])
def test_every_work_type_owes_a_trigger(work_type: str) -> None:
    """No type is exempt, because the exemption would key on a field the author writes.

    Owner, 2026-09-08: exempting the types that "do not need it" invites an agent facing
    a trigger it cannot write to reclassify the record instead. A gate whose exemption
    the gated party controls measures nothing.
    """
    assert invest.TRIGGER_HEADING in invest.required_conditions(work_type)


def test_a_bare_acceptance_heading_satisfies_nothing() -> None:
    """Six active records carried the heading and zero criteria when this was measured."""
    missing = invest.missing_sections(
        {"description": "## Trigger\n\n" + _PATCHING + "\n## Acceptance Criteria\n"},
        (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING),
    )
    assert invest.ACCEPTANCE_HEADING in missing


def test_a_todo_acceptance_criterion_satisfies_nothing() -> None:
    """`compose_body` writes its unfilled hint as a bullet, so the bullet must not count."""
    body = f"{_PATCHING}\n## Acceptance Criteria\n\n- TODO: Given x when y then z\n"
    missing = invest.missing_sections(
        {"description": body}, (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING)
    )
    assert invest.ACCEPTANCE_HEADING in missing


def test_either_acceptance_carrier_satisfies_testable() -> None:
    """The body section and the structured field are both legitimate (basicly-58iu).

    82 active records carried their criteria in the field alone when this was measured,
    and the first filing of basicly-q1ve1fn counted every one of them as missing.
    """
    required = (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING)
    in_body = {"description": f"{_PATCHING}\n## Acceptance Criteria\n\n- given x then y\n"}
    in_field = {"description": _PATCHING, "acceptance_criteria": "given x then y"}
    assert invest.missing_sections(in_body, required) == ()
    assert invest.missing_sections(in_field, required) == ()


def test_a_trigger_is_missing_by_its_own_name() -> None:
    """The refusal names the section, so the author is not left to guess the remedy."""
    record = {"description": "## Acceptance Criteria\n\n- given x then y\n"}
    missing = invest.missing_sections(record, (invest.TRIGGER_HEADING, invest.ACCEPTANCE_HEADING))
    assert missing == (invest.TRIGGER_HEADING,)


def test_the_remedy_names_both_voices_and_demands_neither() -> None:
    """An author shown only the persona form learns to fabricate a persona."""
    remedy = invest.trigger_remedy()
    assert invest.JOB_STORY_EXAMPLE in remedy
    assert invest.USER_STORY_EXAMPLE in remedy

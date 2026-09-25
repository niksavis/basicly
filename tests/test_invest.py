from __future__ import annotations

import importlib.util
from pathlib import Path

import pytest

from basicly import invest
from tests.plan_fixtures import install_kit

KIT_DIR = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "tracker"


def _kit_shaping():
    spec = importlib.util.spec_from_file_location(
        "shaping_under_invest_test", KIT_DIR / "shaping.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


shaping = _kit_shaping()

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


@pytest.mark.parametrize(
    "text",
    [
        "the gate exits zero",
        "TODO",
        "<fill me in>",
        "`show <id>` prints the record",
        "`show <id>` prints <what is checked>",
        "`TODO` names the marker",
        "`unclosed <id> quote",
        shaping.JOB_STORY_EXAMPLE,
    ],
)
def test_the_engine_and_the_kit_agree_on_an_unfilled_placeholder(text: str) -> None:
    assert invest.unfilled(text) == shaping.unfilled(text)


def test_a_job_story_trigger_states_a_trigger_with_no_persona() -> None:
    assert shaping.trigger_voice(_PATCHING) == "job"


def test_a_user_story_trigger_states_a_trigger_too() -> None:
    assert shaping.trigger_voice(_USER_VOICE) == "user"


def test_a_record_may_carry_both_voices() -> None:
    assert shaping.trigger_voice(f"{_PATCHING}\n{_USER_VOICE}") is not None


def test_prose_under_the_trigger_heading_states_no_trigger() -> None:

    body = "## Trigger\n\nThe board goes stale against its template and nobody notices.\n"
    assert shaping.trigger_voice(body) is None


def test_the_scaffold_placeholder_states_no_trigger() -> None:

    assert shaping.trigger_voice(shaping.JOB_STORY_EXAMPLE) is None
    assert shaping.trigger_voice(shaping.USER_STORY_EXAMPLE) is None


def test_a_body_with_no_trigger_section_states_no_trigger() -> None:
    assert shaping.trigger_voice("## Acceptance Criteria\n\n- given x then y\n") is None
    assert shaping.trigger_voice("") is None


@pytest.mark.parametrize("work_type", ["bug", "chore", "task", "feature", "epic"])
def test_every_work_type_owes_a_trigger(work_type: str) -> None:

    assert invest.TRIGGER_HEADING in invest.required_conditions(work_type)


def _missing(tmp_path: Path, record: dict[str, str]) -> tuple[str, ...]:
    install_kit(tmp_path)
    return invest.missing_for(record, "task", tmp_path, declared={})


def test_a_bare_acceptance_heading_satisfies_nothing(tmp_path: Path) -> None:
    record = {"description": _PATCHING + "\n## Acceptance Criteria\n"}
    assert invest.ACCEPTANCE_HEADING in _missing(tmp_path, record)


def test_a_todo_acceptance_criterion_satisfies_nothing(tmp_path: Path) -> None:
    record = {"description": _PATCHING, "acceptance_criteria": "- TODO: Given x when y then z"}
    assert invest.ACCEPTANCE_HEADING in _missing(tmp_path, record)


def test_only_the_typed_field_satisfies_testable_on_an_open_record(tmp_path: Path) -> None:
    in_body = {
        "description": f"{_PATCHING}\n## Acceptance Criteria\n\n- given x then y\n",
        "requirements": "- a requirement",
    }
    in_field = {
        "description": _PATCHING,
        "acceptance_criteria": "given x then y",
        "requirements": "- a requirement",
    }
    assert _missing(tmp_path, in_body) == (invest.ACCEPTANCE_HEADING,)
    assert _missing(tmp_path, in_field) == ()


def test_a_trigger_is_missing_by_its_own_name(tmp_path: Path) -> None:
    record = {
        "description": "no story here",
        "acceptance_criteria": "given x then y",
        "requirements": "- a requirement",
    }
    assert _missing(tmp_path, record) == (invest.TRIGGER_HEADING,)


def test_the_engine_story_patterns_are_the_kit_patterns() -> None:
    assert invest._JOB_STORY.pattern == shaping._JOB_STORY.pattern
    assert invest._USER_STORY.pattern == shaping._USER_STORY.pattern
    assert invest._PLACEHOLDER.pattern == shaping._PLACEHOLDER.pattern


def test_the_remedy_names_both_voices_and_demands_neither() -> None:
    remedy = invest.trigger_remedy()
    assert invest.JOB_STORY_EXAMPLE in remedy
    assert invest.USER_STORY_EXAMPLE in remedy

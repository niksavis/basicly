from __future__ import annotations

import importlib.util
from pathlib import Path
from typing import Any

import pytest

from basicly import invest, owned_store, owned_write, tracker, tracker_query
from tests.plan_fixtures import install_kit
from tests.tracker_process_fixture import DEBTS, recorded_review

KIT_DIR = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "tracker"


def test_bulk_owed_reads_one_current_event_corpus_without_losing_process_debt(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    install_kit(tmp_path)
    ledger = tracker.ledger_dir(tmp_path)
    ledger.mkdir(parents=True, exist_ok=True)
    (ledger / "template.json").write_text('{"prefix":"test"}', encoding="utf-8")
    records = [owned_write.create(tmp_path, ["create", title]) for title in ("first", "second")]
    events = tracker.kit(tmp_path, "events")
    states = events.fold(events.read_events(ledger)[0]).records
    original = events.read_events
    reads = []

    def read_once(*args: Any, **kwargs: Any) -> Any:
        reads.append(args)
        return original(*args, **kwargs)

    monkeypatch.setattr(events, "read_events", read_once)
    owed = invest.owed([states[record] for record in records], tmp_path)
    assert len(reads) == 1
    assert all(set(DEBTS) <= set(owed[record]) for record in records)
    invest.missing_for({**states[records[0]].fields, "id": records[0]}, "task", tmp_path)
    assert len(reads) == 2


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
    assert _missing(tmp_path, in_body) == (invest.ACCEPTANCE_HEADING, *DEBTS)
    assert _missing(tmp_path, in_field) == DEBTS


def test_a_trigger_is_missing_by_its_own_name(tmp_path: Path) -> None:
    record = {
        "description": "no story here",
        "acceptance_criteria": "given x then y",
        "requirements": "- a requirement",
    }
    assert _missing(tmp_path, record) == (invest.TRIGGER_HEADING, *DEBTS)


@pytest.mark.parametrize("body", [_PATCHING, _USER_VOICE])
def test_the_engine_keeps_the_complete_trigger_sentence(body: str) -> None:
    expected = body.split("\n\n", 1)[1].strip()
    assert invest.trigger_sentence(body) == expected


def test_the_remedy_names_both_voices_and_demands_neither() -> None:
    remedy = invest.trigger_remedy()
    assert shaping.JOB_STORY_EXAMPLE in remedy
    assert shaping.USER_STORY_EXAMPLE in remedy


@pytest.mark.parametrize(
    "description",
    [
        "When, I want, so I can",
        "When a release starts, I want a patch, so I can <outcome>.",
        "As a maintainer, I want a patch, so that <benefit>.",
        "As a maintainer, I want a patch, so that.",
    ],
)
def test_integrated_readiness_refuses_incomplete_story_intent(
    tmp_path: Path, description: str
) -> None:
    record = {
        "description": description,
        "acceptance_criteria": "the gate exits zero",
        "requirements": "standard library only",
    }
    assert invest.trigger_sentence(description) == ""
    assert _missing(tmp_path, record) == (invest.TRIGGER_HEADING, *DEBTS)


@pytest.mark.parametrize(
    "description",
    [
        "When a release starts, I want a patch, so I can <outcome>.",
        "As a maintainer, I want a patch, so that.",
    ],
)
def test_integrated_capture_ready_and_claim_use_shared_intent_rules(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, description: str
) -> None:
    install_kit(tmp_path)
    tracker.ledger_dir(tmp_path).mkdir(parents=True)
    tracker.set_ledger_prefix(tmp_path, "demo")
    monkeypatch.setenv("GIT_AUTHOR_NAME", "agent")
    record = owned_write.create(
        tmp_path,
        [
            "create",
            "capture an idea",
            "--description",
            description,
            "--acceptance",
            "the gate exits zero",
            "--requirements",
            "standard library only",
        ],
    )
    before = tracker.kit(tmp_path).read_ledger(tracker.ledger_dir(tmp_path))
    assert tracker.owed_of(tmp_path, record)["blocking"] == [invest.TRIGGER_HEADING, *DEBTS]
    assert tracker_query.ready_report(tmp_path)["count"] == 0
    with pytest.raises(owned_store.TrackerDivergenceError, match="Trigger"):
        owned_write.append(tmp_path, ["update", record, "--status", "in_progress"])
    assert tracker.kit(tmp_path).read_ledger(tracker.ledger_dir(tmp_path)) == before
    complete = "When a release starts, I want a patch, so I can ship safely."
    owned_write.append(tmp_path, ["update", record, "--description", complete])
    recorded_review(owned_store.kit(tmp_path, "commands"), tracker.ledger_dir(tmp_path), record)
    assert tracker_query.ready_report(tmp_path)["count"] == 1
    owned_write.append(tmp_path, ["update", record, "--status", "in_progress"])
    assert (tracker.read_record(tmp_path, record) or {})["status"] == "in_progress"


@pytest.mark.parametrize(
    "description",
    [
        "When I capture a todo, I want to save a task, so I can remember work.",
        "As a todo app user, I want a task saved, so that I remember work.",
    ],
)
def test_integrated_readiness_accepts_ordinary_todo_words(tmp_path: Path, description: str) -> None:
    record = {
        "description": description,
        "acceptance_criteria": "the saved task is readable",
        "requirements": "standard library only",
    }
    assert invest.trigger_sentence(description) == description
    assert _missing(tmp_path, record) == DEBTS

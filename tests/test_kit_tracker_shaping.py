from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_shaping_test_cli")
shaping = _load(REPO_ROOT / KIT_RELATIVE / "shaping.py", "kit_shaping_test_shaping")

TRIGGER = "When a record is picked up, I want its criteria present, so I can verify against them."
PERSONA = "As a maintainer, I want the criteria present, so that I can verify against them."

PROSE = f"""{TRIGGER}

## Acceptance Criteria

- the gate exits zero

## Requirements

- standard library only
"""


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    directory.mkdir()
    return directory


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict]:
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


def _create(capsys: pytest.CaptureFixture[str], ledger: Path, *extra: str) -> tuple[str, dict]:
    code, report = _run(
        capsys, "create", str(ledger), "--prefix", "demo", "--title", "a record", *extra
    )
    assert code == cli.EXIT_OK
    return report["record"], report


def test_a_bare_record_owes_all_three_sections(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report = _create(capsys, ledger)

    assert report["owed"] == [
        shaping.TRIGGER_HEADING,
        shaping.ACCEPTANCE_HEADING,
        shaping.REQUIREMENTS_HEADING,
    ]
    assert report["remedy"]


def test_the_flags_shape_a_record_with_no_prose_at_all(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report = _create(
        capsys,
        ledger,
        "--description",
        TRIGGER,
        "--acceptance",
        "the gate exits zero",
        "--requirements",
        "standard library only",
    )

    assert report["owed"] == []
    assert report["remedy"] == ""


def test_a_description_holding_a_typed_heading_is_refused_naming_the_flag(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["create", str(ledger), "--prefix", "acme", "--title", "t", "--description", PROSE]
    code, report = _run(capsys, *argv)

    assert code == cli.EXIT_REFUSED
    assert "--acceptance" in report["refused"]


def test_a_heading_still_reads_on_a_closed_record(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record = "acme-lgcy"
    legacy = {"title": "an imported record", "description": PROSE}
    cli.events.append(
        ledger,
        [
            cli.events.Draft(record, cli.events.KIND_CREATED, legacy),
            cli.events.Draft(record, cli.events.KIND_STATUS, {"status": "open"}),
        ],
    )
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_REFUSED

    _run(capsys, "close", str(ledger), record, "--reason", "shipped")

    code, report = _run(capsys, "dor", str(ledger), record)
    assert code == cli.EXIT_OK
    assert report["owed"] == [], (
        "a closed record is evidence and will never be verified again, so the heading "
        "stays readable rather than being rewritten"
    )


def test_either_story_voice_satisfies_the_trigger() -> None:
    assert shaping.trigger_voice(TRIGGER) == shaping.JOB_VOICE
    assert shaping.trigger_voice(PERSONA) == shaping.USER_VOICE


def test_a_placeholder_is_absent_rather_than_present() -> None:
    assert shaping.trigger_voice(shaping.JOB_STORY_EXAMPLE) is None, (
        "the example is all placeholders, so pasting it must not satisfy the trigger"
    )
    assert shaping.owed({"acceptance_criteria": "TODO", "requirements": "<fill me in>"}) == (
        shaping.TRIGGER_HEADING,
        shaping.ACCEPTANCE_HEADING,
        shaping.REQUIREMENTS_HEADING,
    )


def test_the_gate_refuses_an_unshaped_record_and_names_what_is_missing(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record, _ = _create(capsys, ledger)

    code, report = _run(capsys, "dor", str(ledger), record)

    assert code == cli.EXIT_REFUSED
    assert report["ready"] is False
    assert shaping.ACCEPTANCE_HEADING in report["owed"]


def test_the_gate_passes_a_shaped_record(ledger: Path, capsys: pytest.CaptureFixture[str]) -> None:
    record, _ = _create(
        capsys,
        ledger,
        "--description",
        TRIGGER,
        "--acceptance",
        "the gate exits zero",
        "--requirements",
        "standard library only",
    )

    code, report = _run(capsys, "dor", str(ledger), record)

    assert code == cli.EXIT_OK
    assert report["ready"] is True
    assert report["owed"] == []


def test_an_update_can_shape_a_record_the_gate_refused(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record, _ = _create(capsys, ledger)
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_REFUSED

    _, updated = _run(
        capsys,
        "update",
        str(ledger),
        record,
        "--description",
        TRIGGER,
        "--acceptance",
        "the gate exits zero",
        "--requirements",
        "standard library only",
    )

    assert updated["owed"] == []
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_OK


def test_a_child_is_shaped_by_the_same_flags(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    parent, _ = _create(capsys, ledger, "--description", TRIGGER)

    _, report = _run(
        capsys,
        "child",
        str(ledger),
        parent,
        "--title",
        "a child",
        "--description",
        TRIGGER,
        "--acceptance",
        "the child is shaped too",
        "--requirements",
        "no engine import",
    )

    assert report["owed"] == []


LEGACY = {
    "description": TRIGGER,
    "acceptance_criteria": "the gate exits zero",
}


def test_a_record_the_kit_minted_is_refused_for_missing_requirements(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record, _ = _create(
        capsys, ledger, "--description", TRIGGER, "--acceptance", "the gate exits zero"
    )

    code, report = _run(capsys, "dor", str(ledger), record)

    assert code == cli.EXIT_REFUSED
    assert shaping.REQUIREMENTS_HEADING in report["blocking"]


def test_a_record_minted_before_the_rule_is_refused_for_requirements() -> None:
    assert shaping.REQUIREMENTS_HEADING in shaping.owed(LEGACY), (
        "the debt stays visible, so a board still shows it"
    )
    assert shaping.refused(LEGACY) == (shaping.REQUIREMENTS_HEADING,), (
        "the rule holds every record to the requirements, whenever it was minted"
    )
    assert not shaping.shaped(LEGACY)


def test_the_marker_is_absent_rather_than_dated_on_a_legacy_record() -> None:
    assert not shaping.minted_under_the_rule(LEGACY)
    assert shaping.minted_under_the_rule({shaping.SHAPED_UNDER_FIELD: shaping.SHAPING_RULE})


def test_a_minted_record_still_owes_the_trigger_and_the_criteria(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    record, report = _create(capsys, ledger)

    assert report["blocking"] == [
        shaping.TRIGGER_HEADING,
        shaping.ACCEPTANCE_HEADING,
        shaping.REQUIREMENTS_HEADING,
    ]
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_REFUSED


def test_a_list_of_criteria_counts_as_stated() -> None:
    assert (
        shaping.owed({
            "description": TRIGGER,
            "acceptance_criteria": ["one", "two"],
            "requirements": ["a requirement"],
        })
        == ()
    )


def test_an_empty_list_owes_the_section() -> None:
    assert shaping.ACCEPTANCE_HEADING in shaping.owed({
        "description": TRIGGER,
        "acceptance_criteria": [],
        "requirements": ["a requirement"],
    })

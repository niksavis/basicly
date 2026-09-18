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


def test_prose_headings_still_count_so_an_existing_record_keeps_working(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _, report = _create(capsys, ledger, "--description", PROSE)

    assert report["owed"] == []


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
    record, _ = _create(capsys, ledger, "--description", PROSE)

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
    parent, _ = _create(capsys, ledger, "--description", PROSE)

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

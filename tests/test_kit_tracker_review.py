from __future__ import annotations

import importlib.util
import json
import sys
from dataclasses import replace
from pathlib import Path
from typing import Any

import pytest

from tests.tracker_process_fixture import recorded_review

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
TRIGGER = "When a person drafts a story, I want an agent to review it, so I can trust it."
SHAPED = ("--description", TRIGGER, "--acceptance", "- it is reviewed", "--requirements", "- none")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_review")


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> dict[str, Any]:
    capsys.readouterr()
    cli.main(list(argv))
    return json.loads(capsys.readouterr().out)


def _create(capsys: pytest.CaptureFixture[str], ledger: Path, *extra: str) -> str:
    made = _run(capsys, "create", str(ledger), "--prefix", "acme", "--title", "draft", *extra)
    return made["record"]


def test_a_person_cannot_clear_the_review_mark(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _create(capsys, ledger, *SHAPED, "--field", "labels=refine")

    report = _run(capsys, "update", str(ledger), record, "--remove-label", "refine")

    assert "waits for an agent review" in report["refused"]


def test_an_agent_clears_the_mark_only_when_nothing_is_owed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger"
    record = _create(capsys, ledger, "--field", "labels=refine")
    monkeypatch.setenv("AI_AGENT", "refiner")

    early = _run(capsys, "update", str(ledger), record, "--remove-label", "refine")
    _run(capsys, "update", str(ledger), record, *SHAPED)
    recorded_review(cli, ledger, record)
    done = _run(capsys, "update", str(ledger), record, "--remove-label", "refine")

    assert "still owes" in early["refused"]
    assert not isinstance(done.get("refused"), str), done


@pytest.mark.parametrize(
    ("extra", "reason"),
    [(("--field", "labels=refine", *SHAPED), "waits for an agent review"), ((), "it owes")],
)
def test_work_cannot_start_on_a_story_no_agent_reviewed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], extra: tuple[str, ...], reason: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _create(capsys, ledger, *extra)

    claimed = _run(capsys, "claim", str(ledger), record, "--to", "sam")
    started = _run(capsys, "update", str(ledger), record, "--status", "in_progress")

    assert reason in claimed["refused"] and "cannot start" in claimed["refused"]
    assert "cannot start" in started["refused"]


def test_a_story_waiting_for_review_can_still_be_reserved(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _create(capsys, ledger, "--field", "labels=refine")

    reserved = _run(capsys, "assign", str(ledger), record, "--to", "sam")
    shown = _run(capsys, "show", str(ledger), record)

    assert not isinstance(reserved.get("refused"), str), reserved
    assert (shown["status"], shown["holder"]["name"]) == ("open", "sam")


def test_a_conversation_sequence_with_two_forked_comments_cannot_certify_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    from_payload = {
        "independent": "The task uses one existing operation.",
        "negotiable": "The implementation may change.",
        "valuable": "The saved result can be read.",
        "estimable": "The fixture fixes the input and result.",
        "small": "The task has one observable result.",
        "testable": "The check observes the saved result.",
    }
    ledger = tmp_path / "ledger"
    record = _create(capsys, ledger, *SHAPED)
    _run(capsys, "comment", str(ledger), record, "Agree to check the saved result.")
    note = next(
        event
        for event in reversed(cli.events.read_events(ledger)[0])
        if event.kind in cli.events.PROSE_KINDS
    )
    fork = replace(
        note,
        id=record + "#ev-aaaaaaaaaa",
        payload={"text": "A competing branch records a different agreement."},
    )
    log = next(ledger.glob(cli.events.LOG_GLOB))
    cli.events.append_lines(log, [cli.events.to_json(fork)])
    evidence = {
        "invest": from_payload,
        "conversation": [note.seq],
        "checks": [
            {
                "criterion": "it is reviewed",
                "command": ["fixture-check"],
                "expected": "The result is reviewed.",
            }
        ],
    }
    before = cli.events.read_events(ledger)[0]
    report = _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(evidence))
    assert "Conversation" in report["refused"]
    assert cli.events.read_events(ledger)[0] == before


def test_cancelled_raw_card_does_not_authorize_a_code_commit(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger"
    record = _create(capsys, ledger)
    monkeypatch.setenv("BASICLY_HOLDER", "sam")
    assert "refused" not in _run(capsys, "assign", str(ledger), record, "--to", "sam")
    assert "refused" not in _run(
        capsys,
        "close",
        str(ledger),
        record,
        "--resolution",
        "cancelled",
        "--reason",
        "This idea will not be built.",
    )
    message = tmp_path / "message.txt"
    message.write_text(f"feat: implement captured idea {record}\n", encoding="utf-8")
    report = _run(capsys, "commit-check", str(ledger), str(message), "src/application.py")
    assert "cancelled" in report["refused"]
    assert "none of the records" in report["refused"]

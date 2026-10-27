from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import tracker
from tests import flipped_tracker
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load
from tests.tracker_process_fixture import INVEST

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_process_evidence_cli")
TRIGGER = "When a task arrives, I want it saved, so I can find the work later."


def _run(capsys: pytest.CaptureFixture[str], *args: str) -> tuple[int, dict]:
    result = cli.main(list(args))
    return result, json.loads(capsys.readouterr().out)


def _card(ledger: Path, capsys: pytest.CaptureFixture[str]) -> str:
    result, saved = _run(
        capsys,
        "create",
        str(ledger),
        "--prefix",
        "demo",
        "--title",
        "save a task",
        "--description",
        TRIGGER,
        "--acceptance",
        "When a task is saved, show shall return its title.",
        "--requirements",
        "Use the existing ledger.",
    )
    assert result == cli.EXIT_OK
    return saved["record"]


def test_a_structured_card_without_process_evidence_is_not_ready(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    result, verdict = _run(capsys, "dor", str(ledger), record)
    assert result == cli.EXIT_REFUSED
    assert verdict["ready"] is False
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 0


def test_a_structured_card_without_process_evidence_cannot_be_claimed(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    before = cli.events.read_events(ledger)[0]
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_REFUSED
    assert cli.events.read_events(ledger)[0] == before


@pytest.mark.parametrize("generic", [False, True])
def test_completed_close_needs_confirmation_even_with_a_delivered_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], generic: bool
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    before = cli.events.read_events(ledger)[0]
    args = (
        [
            "update",
            str(ledger),
            record,
            "--status",
            "closed",
            "--field",
            "close_reason=Saved tasks are readable.",
        ]
        if generic
        else ["close", str(ledger), record, "--reason", "Saved tasks are readable."]
    )
    assert _run(capsys, *args)[0] == cli.EXIT_REFUSED
    assert cli.events.read_events(ledger)[0] == before


def _review(ledger: Path, record: str, capsys: pytest.CaptureFixture[str]) -> dict:
    assert (
        _run(
            capsys,
            "comment",
            str(ledger),
            record,
            "Use the existing save and show flow; confirm saved titles are readable.",
        )[0]
        == cli.EXIT_OK
    )
    found = cli.events.read_events(ledger)[0]
    reference = next(event.seq for event in reversed(found) if event.kind in cli.events.PROSE_KINDS)
    evidence = {
        "invest": dict(INVEST),
        "conversation": [reference],
        "checks": [
            {
                "criterion": "When a task is saved, show shall return its title.",
                "command": ["python", "check_saved_title.py"],
                "expected": "The saved title appears in show.",
            }
        ],
    }
    result, report = _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(evidence))
    assert result == cli.EXIT_OK, report
    return evidence


def test_review_claim_confirmation_and_completed_close(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    evidence = _review(ledger, record, capsys)
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_OK
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    assert (
        _run(capsys, "close", str(ledger), record, "--reason", "Saved titles are readable.")[0]
        == cli.EXIT_REFUSED
    )
    checks = [
        {
            "criterion": check["criterion"],
            "command": check["command"],
            "result": "Saved title appeared in show.",
            "exit_code": 0,
        }
        for check in evidence["checks"]
    ]
    assert (
        _run(capsys, "confirm", str(ledger), record, "--evidence", json.dumps({"checks": checks}))[
            0
        ]
        == cli.EXIT_OK
    )
    assert (
        _run(capsys, "close", str(ledger), record, "--reason", "Saved titles are readable.")[0]
        == cli.EXIT_OK
    )


@pytest.mark.parametrize(
    "field,value",
    [("title", "save and retrieve a task"), ("requirements", "Use the ledger and show command.")],
)
def test_semantic_edit_invalidates_recorded_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], field: str, value: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    _review(ledger, record, capsys)
    assert (
        _run(capsys, "update", str(ledger), record, "--field", f"{field}={value}")[0] == cli.EXIT_OK
    )
    result, report = _run(capsys, "dor", str(ledger), record)
    assert result == cli.EXIT_REFUSED
    assert "## Conversation" in report["owed"]


def test_raw_card_can_be_cancelled_with_a_reason(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    assert (
        _run(
            capsys,
            "close",
            str(ledger),
            record,
            "--resolution",
            "cancelled",
            "--reason",
            "The requested work is no longer needed.",
        )[0]
        == cli.EXIT_OK
    )


@pytest.mark.parametrize(
    "mutation",
    [
        "missing_rationale",
        "placeholder",
        "foreign_comment",
        "wrong_command",
        "missing_check",
        "supplied_revision",
    ],
)
def test_review_refuses_incomplete_or_forged_evidence(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    evidence = _review(ledger, record, capsys)
    if mutation == "missing_rationale":
        del evidence["invest"]["small"]
    elif mutation == "placeholder":
        evidence["invest"]["small"] = "<explain scope>"
    elif mutation == "foreign_comment":
        evidence["conversation"] = [1]
    elif mutation == "wrong_command":
        evidence["checks"][0]["command"] = "python check_saved_title.py"
    elif mutation == "missing_check":
        evidence["checks"] = []
    else:
        evidence["revision"] = "caller-chosen"
    before = cli.events.read_events(ledger)[0]
    assert (
        _run(capsys, "review", str(ledger), record, "--evidence", json.dumps(evidence))[0]
        == cli.EXIT_REFUSED
    )
    assert cli.events.read_events(ledger)[0] == before


def test_holder_and_status_changes_preserve_review(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    _review(ledger, record, capsys)
    assert _run(capsys, "assign", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_OK
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_OK


def test_dependency_edit_invalidates_review_and_cancelled_target_remains_unmet(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    target = _card(ledger, capsys)
    _review(ledger, record, capsys)
    assert _run(capsys, "dep", str(ledger), record, target)[0] == cli.EXIT_OK
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_REFUSED
    _review(ledger, record, capsys)
    assert (
        _run(
            capsys,
            "close",
            str(ledger),
            target,
            "--resolution",
            "cancelled",
            "--reason",
            "This prerequisite is abandoned.",
        )[0]
        == cli.EXIT_OK
    )
    assert _run(capsys, "ready", str(ledger))[1]["count"] == 0
    assert (
        _run(capsys, "blocked", str(ledger))[1]["records"][0]["blocked_by"][0]["record"] == target
    )
    assert _run(capsys, "stats", str(ledger))[1]["by_resolution"] == {"cancelled": 1}


def test_integrated_write_uses_the_same_review_and_confirmation_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    (repo / "basicly.toml").write_text(
        '[tracker]\nmode = "owned"\nprefix = "demo"\n', encoding="utf-8"
    )
    monkeypatch.chdir(repo)
    record = _card(tracker.ledger_dir(repo), capsys)
    with pytest.raises(tracker.TrackerDivergenceError, match="cannot complete"):
        tracker.write(repo, ["close", record, "--reason", "Saved tasks are readable."])
    evidence = _review(tracker.ledger_dir(repo), record, capsys)
    tracker.write(repo, ["review", record, "--evidence", json.dumps(evidence)])
    tracker.write(repo, ["update", record, "--status", "in_progress"])
    checks = [
        {
            "criterion": check["criterion"],
            "command": check["command"],
            "result": "Saved title appeared.",
            "exit_code": 0,
        }
        for check in evidence["checks"]
    ]
    tracker.write(repo, ["confirm", record, "--evidence", json.dumps({"checks": checks})])
    tracker.write(repo, ["close", record, "--reason", "Saved tasks are readable."])
    held = tracker.read_record(repo, record)
    assert held is not None
    assert held["close_resolution"] == "completed"


@pytest.mark.parametrize("semantic", [True, False])
def test_review_binds_direct_dependency_contract_but_not_its_status(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], semantic: bool
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    target = _card(ledger, capsys)
    assert _run(capsys, "dep", str(ledger), record, target)[0] == cli.EXIT_OK
    _review(ledger, record, capsys)
    args = (
        ["--acceptance", "When a task is saved, show shall return its title and identifier."]
        if semantic
        else ["--status", "blocked"]
    )
    assert _run(capsys, "update", str(ledger), target, *args)[0] == cli.EXIT_OK
    result, _report = _run(capsys, "dor", str(ledger), record)
    assert result == (cli.EXIT_REFUSED if semantic else cli.EXIT_OK)


@pytest.mark.parametrize("mutation", ["command", "result", "exit_code", "missing_criterion"])
def test_completion_refuses_unmatched_or_failed_checks(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    review = _review(ledger, record, capsys)
    checks = [
        {
            "criterion": check["criterion"],
            "command": check["command"],
            "result": "The saved title appeared.",
            "exit_code": 0,
        }
        for check in review["checks"]
    ]
    if mutation == "command":
        checks[0]["command"] = ["python", "different_check.py"]
    elif mutation == "result":
        checks[0]["result"] = "<observed output>"
    elif mutation == "exit_code":
        checks[0]["exit_code"] = 1
    else:
        checks = []
    before = cli.events.read_events(ledger)[0]
    assert (
        _run(capsys, "confirm", str(ledger), record, "--evidence", json.dumps({"checks": checks}))[
            0
        ]
        == cli.EXIT_REFUSED
    )
    assert cli.events.read_events(ledger)[0] == before


def test_a_generic_field_cannot_forge_review_even_when_template_declares_it(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    (ledger / "template.json").write_text(
        json.dumps({"mode": "extend", "sections": ["## Process Review"]}), encoding="utf-8"
    )
    before = cli.events.read_events(ledger)[0]
    result, report = _run(capsys, "update", str(ledger), record, "--field", "process_review={}")
    assert result == cli.EXIT_REFUSED
    assert "managed evidence" in report["refused"]
    assert cli.events.read_events(ledger)[0] == before


def test_existing_closed_history_is_readable_without_automatic_certification(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    cli.events.append(
        ledger, [cli.events.Draft(record, cli.events.KIND_STATUS, {"status": "closed"})]
    )
    result, shown = _run(capsys, "show", str(ledger), record)
    assert result == cli.EXIT_OK
    assert shown["status"] == "closed"
    assert shown["process"]["review"] == {}
    assert _run(capsys, "stats", str(ledger))[1]["by_resolution"] == {"historical": 1}
    assert _run(capsys, "update", str(ledger), record, "--status", "open")[0] == cli.EXIT_OK
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_REFUSED


@pytest.mark.parametrize("mutation", ["field", "template"])
def test_review_binds_effective_template_and_required_custom_fields(
    tmp_path: Path, capsys: pytest.CaptureFixture[str], mutation: str
) -> None:
    ledger = tmp_path / "ledger"
    record = _card(ledger, capsys)
    template = ledger / "template.json"
    template.write_text(json.dumps({"sections": ["## Risks"]}), encoding="utf-8")
    assert (
        _run(
            capsys,
            "update",
            str(ledger),
            record,
            "--field",
            "risks=The scratch ledger isolates the write.",
        )[0]
        == cli.EXIT_OK
    )
    _review(ledger, record, capsys)
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_OK
    if mutation == "field":
        assert (
            _run(
                capsys,
                "update",
                str(ledger),
                record,
                "--field",
                "risks=Two callers may race; the ledger lock serializes them.",
            )[0]
            == cli.EXIT_OK
        )
    else:
        template.write_text(json.dumps({"sections": []}), encoding="utf-8")
    assert _run(capsys, "dor", str(ledger), record)[0] == cli.EXIT_REFUSED


def test_empty_template_does_not_allow_vacuous_review_claim_or_completion(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    (ledger / "template.json").write_text(
        json.dumps({"mode": "override", "sections": []}), encoding="utf-8"
    )
    result, card = _run(capsys, "create", str(ledger), "--prefix", "demo", "--title", "raw idea")
    assert result == cli.EXIT_OK
    record = card["record"]
    assert "## Confirmation Plan" in card["owed"]
    assert _run(capsys, "claim", str(ledger), record, "--to", "agent")[0] == cli.EXIT_REFUSED
    assert (
        _run(capsys, "close", str(ledger), record, "--reason", "The idea is complete.")[0]
        == cli.EXIT_REFUSED
    )
    _run(
        capsys,
        "comment",
        str(ledger),
        record,
        "Discuss what observable result would establish this idea.",
    )
    reference = max(
        event.seq
        for event in cli.events.read_events(ledger)[0]
        if event.kind in cli.events.PROSE_KINDS
    )
    assert (
        _run(
            capsys,
            "review",
            str(ledger),
            record,
            "--evidence",
            json.dumps({"invest": INVEST, "conversation": [reference], "checks": []}),
        )[0]
        == cli.EXIT_REFUSED
    )

from __future__ import annotations

import json
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_cli_wiring import (
    LEDGER,
    _clone,
    _environment,
    _kit,
    _repo,
    _report,
    cli,
    vault_queue,
)
from tests.test_kit_vault_share import bare

__all__ = ["_environment", "bare"]

CRITERION = "When the fixture check runs, it shall report the saved result."
TRIGGER = "When a team shares a task, I want to save its progress, so I can complete it."
WORKS = ("update", "comment", "child", "dep", "undep", "review", "confirm", "close")
PROTOCOL = ("claim", "unassign")
REFUSED = ("assign", "resolve", "acknowledge-loss", "delete")


def test_every_record_write_form_has_one_vault_contract_class() -> None:
    classified = ("create", *WORKS, *PROTOCOL, *REFUSED)

    assert len(classified) == len(set(classified)) == 15
    assert set(classified) == {"create", *cli._WRITES}


def _run(root: Path, *argv: str) -> dict[str, Any]:
    done = _kit(root, *argv)
    assert done.returncode == 0, done.stdout + done.stderr
    shown = _report(done)
    assert isinstance(shown, dict), shown
    return shown


def _create(root: Path, title: str) -> str:
    return _run(
        root,
        "create",
        LEDGER,
        "--title",
        title,
        "--field",
        "issue_type=task",
        "--description",
        TRIGGER,
        "--acceptance",
        CRITERION,
    )["record"]


def _review() -> dict[str, Any]:
    return {
        "checks": [
            {
                "criterion": CRITERION,
                "command": [Path(sys.executable).name, "-c", "print('saved result')"],
                "expected": "saved result",
            }
        ]
    }


def _confirm(review: dict[str, Any]) -> dict[str, Any]:
    planned = review["checks"][0]
    actual = subprocess.run(planned["command"], capture_output=True, text=True, check=True)
    assert actual.stdout.strip() == planned["expected"]
    return {
        "checks": [
            {
                "criterion": CRITERION,
                "command": planned["command"],
                "result": actual.stdout,
                "exit_code": actual.returncode,
            }
        ]
    }


@pytest.mark.parametrize("verb", WORKS)
def test_a_vault_born_record_can_complete_each_ordinary_write_without_local_events(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _repo(tmp_path, bare)
    record = _create(root, "A vault-born task")
    other = _create(root, "A dependency")
    review = _review()
    if verb in {"confirm", "close"}:
        _run(root, "review", LEDGER, record, "--evidence", json.dumps(review))
    if verb == "close":
        _run(root, "claim", record)
        _run(root, "confirm", LEDGER, record, "--evidence", json.dumps(_confirm(review)))
    if verb == "undep":
        _run(root, "dep", LEDGER, record, other, "--type", "blocks")
    forms = {
        "update": ["--field", "priority=1"],
        "comment": ["A vault note"],
        "child": ["--title", "A child"],
        "dep": [other, "--type", "blocks"],
        "undep": [other, "--type", "blocks"],
        "review": ["--evidence", json.dumps(review)],
        "confirm": ["--evidence", json.dumps(_confirm(review) if verb == "confirm" else {})],
        "close": ["--reason", "The fixture check reported the saved result"],
    }

    written = _run(root, verb, LEDGER, record, *forms[verb])
    shown = _run(root, "show", LEDGER, written["record"])

    checks = {
        "update": lambda: shown["fields"]["priority"] == 1,
        "comment": lambda: any(row["text"] == "A vault note" for row in shown["comment_log"]),
        "child": lambda: (
            written["record"].startswith(record + ".")
            and any(edge["id"] == record for edge in shown["dependencies"])
        ),
        "dep": lambda: any(edge["id"] == other for edge in shown["dependencies"]),
        "undep": lambda: not any(edge["id"] == other for edge in shown["dependencies"]),
        "review": lambda: bool(shown["fields"].get("process_review")),
        "confirm": lambda: bool(shown["fields"].get("process_confirmation")),
        "close": lambda: shown["status"] == "closed",
    }
    assert checks[verb](), shown
    ledger = root / LEDGER
    assert not list(ledger.glob("events-*.jsonl"))
    assert not list(ledger.glob("pending-*.jsonl"))


@pytest.mark.parametrize("verb", REFUSED)
def test_a_retired_or_unsupported_vault_write_names_a_real_remedy_and_writes_nothing(
    tmp_path: Path, bare: Path, verb: str
) -> None:
    root = _repo(tmp_path, bare)
    record = _create(root, "A preserved task")
    clone = _clone(tmp_path, bare)
    queue = clone.folder / vault_queue.QUEUE_NAME
    assert queue.is_file()
    before = queue.read_bytes()
    extra = {
        "assign": ["--to", "teammate"],
        "resolve": [],
        "acknowledge-loss": ["--sequence", "1", "--reason", "lost", "--evidence", "proof"],
        "delete": [],
    }

    done = _kit(root, verb, LEDGER, record, *extra[verb])

    assert done.returncode == 1, done.stdout + done.stderr
    refused = _report(done)["refused"]
    assert verb in refused and record in refused
    assert "\n" not in refused
    assert (queue.read_bytes() if queue.exists() else b"") == before
    remedy = shlex.split(refused.split("run `", 1)[1].split("`", 1)[0])
    if verb == "delete":
        parsed = cli.arguments.parser().parse_args(remedy)
        assert parsed.command == "close" and parsed.resolution == "cancelled"
    else:
        parser = cli.vault_arguments.FORMS[remedy[0]]()
        parsed = parser.parse_args([*remedy, "--vault", "vault.git"])
        if verb == "assign":
            assert parsed.command in {"claim-vault", "unassign-vault"}
        else:
            assert parsed.command == "fsck-vault" and parsed.repair


@pytest.mark.parametrize("verb", PROTOCOL)
def test_a_vault_ledger_alias_preserves_the_native_protocol_report_and_exit(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, verb: str
) -> None:
    root = _repo(tmp_path, bare)
    record = _create(root, "A claimed task")
    _run(root, "claim", record)
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "another-session")

    native = _kit(root, verb, record)
    alias = _kit(root, verb, LEDGER, record)

    assert native.returncode == alias.returncode == 3, (native.stdout, alias.stdout)
    original, forwarded = _report(native), _report(alias)
    assert set(original) == set(forwarded)
    original.pop("timing", None)
    forwarded.pop("timing", None)
    assert original == forwarded

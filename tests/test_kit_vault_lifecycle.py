from __future__ import annotations

import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import owned_store, owned_write
from tests.test_kit_consumer_install import _wheel
from tests.test_kit_vault_cli_wiring import (
    LEDGER,
    _clone,
    _configure,
    _environment,
    _kit,
    _owned_repo,
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


@pytest.mark.parametrize("form", ["native-comment", "ledger-update"])
def test_an_absent_vault_record_is_named_and_no_event_is_queued(
    tmp_path: Path, bare: Path, form: str
) -> None:
    root = _repo(tmp_path, bare)
    existing = _create(root, "An existing task")
    missing = "acme-missing"
    assert existing != missing
    queue = _clone(tmp_path, bare).folder / vault_queue.QUEUE_NAME
    assert queue.is_file()
    before = queue.read_bytes()
    forms = {
        "native-comment": ["comment", missing, "--text", "A note"],
        "ledger-update": ["update", LEDGER, missing, "--field", "priority=1"],
    }

    done = _kit(root, *forms[form])

    assert done.returncode == 1, done.stdout + done.stderr
    refused = _report(done)["refused"]
    assert missing in refused
    assert "\n" not in refused
    assert queue.read_bytes() == before


def test_the_engine_completes_a_vault_born_lifecycle_without_local_events(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    root = _owned_repo(tmp_path)
    _configure(root, bare)
    other = _create(root, "A task created by the kit")

    record = owned_write.create(
        root,
        ["create", "An engine task", "-t", "task", "-d", TRIGGER, "--acceptance", CRITERION],
    )
    assert record.startswith("acme-") and record != other
    owned_write.append(root, ["update", record, "-p", "1"])
    owned_write.append(root, ["comments", "add", record, "An engine note"])
    owned_write.append(root, ["dep", "add", record, other, "-t", "related"])
    review = _review()
    owned_write.append(root, ["review", record, "--evidence", json.dumps(review)])
    _run(root, "claim", record)
    owned_write.append(root, ["confirm", record, "--evidence", json.dumps(_confirm(review))])
    owned_write.append(root, ["close", record, "--reason", "The saved check passed"])

    shown = _run(root, "show", LEDGER, record)
    assert shown["status"] == "closed" and shown["fields"]["priority"] == 1
    assert any(row["text"] == "An engine note" for row in shown["comment_log"])
    assert any(edge["id"] == other for edge in shown["dependencies"])
    assert not list(owned_store.ledger_dir(root).glob("*.jsonl"))


@pytest.mark.fresh_runtime
def test_the_installed_tracker_completes_the_vault_lifecycle_without_the_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("AI_AGENT", "fixture")
    vault = tmp_path / "vault.git"
    subprocess.run(["git", "init", "--bare", "-q", str(vault)], check=True)
    root = _repo(tmp_path, vault)
    wheel = _wheel("tracker", tmp_path / "dist")
    base = ["uvx", "--from", str(wheel)]
    probe = subprocess.run(
        [
            *base,
            "python",
            "-I",
            "-c",
            "import importlib.util; assert importlib.util.find_spec('basicly') is None; "
            "assert importlib.util.find_spec('basicly_tracker') is not None",
        ],
        cwd=root,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        check=False,
    )
    assert probe.returncode == 0, probe.stdout + probe.stderr

    def call(*argv: str) -> dict[str, Any]:
        done = subprocess.run(
            [*base, "basicly-tracker", *argv],
            cwd=root,
            env=dict(os.environ),
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 0, done.stdout + done.stderr
        return json.loads(done.stdout)

    call("vault-init", "--vault", str(vault), "--yes")
    record = call(
        "create",
        LEDGER,
        "--title",
        "A standalone task",
        "--field",
        "issue_type=task",
        "--description",
        TRIGGER,
        "--acceptance",
        CRITERION,
    )["record"]
    call("update", LEDGER, record, "--field", "priority=1")
    review = _review()
    call("review", LEDGER, record, "--evidence", json.dumps(review))
    call("claim", record)
    call("confirm", LEDGER, record, "--evidence", json.dumps(_confirm(review)))
    call("close", LEDGER, record, "--reason", "The standalone check passed")
    shown = call("show", LEDGER, record)

    assert shown["status"] == "closed" and shown["fields"]["priority"] == 1
    assert not list((root / LEDGER).glob("*.jsonl"))
    assert not (root / "basicly.toml").exists()
    assert not (root / ".basicly" / "core").exists()

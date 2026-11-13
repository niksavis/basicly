from __future__ import annotations

import importlib.util
import json
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from tests.test_kit_board_server import cli, server

ROOT = Path(__file__).resolve().parents[1]
RECORD = "demo-a1b2"
TAKE = "unassign demo-a1b2 --take, then claim demo-a1b2"


def _refusal() -> dict[str, Any]:
    return {
        "exit": 3,
        "record": RECORD,
        "holder": {"writer": "writer-id", "session": "session-id", "name": "sam"},
        "next": TAKE,
    }


def _refuse(*_args: Any, **_kwargs: Any) -> Any:
    raise cli.commands.vault_write.VaultRefusedError(_refusal())


def test_a_board_claim_refusal_names_the_holder_and_remedy_in_one_sentence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    monkeypatch.setattr(cli.commands.vault_write, "claim", _refuse)

    status, shown = server.answer(["claim", str(ledger), RECORD], None)

    assert status == 422
    assert shown["holder"]["name"] == "sam"
    assert "sam" in shown["refused"]
    assert TAKE in shown["refused"]
    assert "\n" not in shown["refused"]
    assert str(tmp_path) not in shown["refused"]


def test_the_claim_hook_prints_the_same_readable_vault_refusal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    ledger.mkdir()
    monkeypatch.setattr(cli.commands.vault_write, "claim", _refuse)
    args = cli.arguments.parser().parse_args(["claim", str(ledger), RECORD])
    code, report = cli.invoke(args)
    path = ROOT / ".basicly" / "core" / "hooks" / "tracker-claim.py"
    spec = importlib.util.spec_from_file_location("cut_c_claim_hook", path)
    assert spec and spec.loader
    hook = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(hook)
    monkeypatch.setattr(hook, "_project_root", lambda: tmp_path)
    monkeypatch.setattr(hook, "_exempt", lambda *_: False)
    monkeypatch.setattr(hook, "_locate", lambda: SimpleNamespace(locate=lambda *_: object()))
    done = subprocess.CompletedProcess([], code, json.dumps(report), "")
    monkeypatch.setattr(hook, "_check_staged_claim", lambda *_: done)

    assert hook.main(["message.txt"]) == 1
    printed = capsys.readouterr().err

    assert len(printed.splitlines()) == 1
    assert "sam" in printed and TAKE in printed
    assert '{"' not in printed
    assert str(tmp_path) not in printed


@pytest.mark.parametrize("command", ["claim-vault", "unassign-vault"])
def test_native_vault_refusals_keep_their_exit_and_readable_message(
    monkeypatch: pytest.MonkeyPatch, command: str
) -> None:
    monkeypatch.setitem(cli.vault_cli.HANDLERS, command, lambda *_: _refusal())

    code, report = cli.invoke(SimpleNamespace(command=command))

    assert code == 3
    assert "sam" in report["refused"]
    assert TAKE in report["refused"]


@pytest.mark.parametrize(
    ("code", "fields", "expected"),
    [
        (1, {"fix": "set the session\nthen retry"}, "set the session then retry"),
        (4, {"unsettled": "a transport detail"}, "sync"),
        (5, {"check": "/private/machine/vault"}, "connection"),
        (6, {"log": "/private/machine/traceback.log"}, "sync"),
        (7, {"holder": {"name": "sam"}}, "already held by this session"),
    ],
)
def test_other_vault_refusals_keep_internal_details_out_of_the_sentence(
    monkeypatch: pytest.MonkeyPatch, code: int, fields: dict[str, Any], expected: str
) -> None:
    report = {"exit": code, "record": RECORD, **fields}
    monkeypatch.setitem(cli.vault_cli.HANDLERS, "claim-vault", lambda *_: dict(report))

    actual, shown = cli.invoke(SimpleNamespace(command="claim-vault"))

    assert actual == code
    assert expected in shown["refused"]
    assert "\n" not in shown["refused"]
    assert "/private/machine" not in shown["refused"]
    assert "Traceback" not in shown["refused"]

from __future__ import annotations

import shlex
from pathlib import Path

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


def test_ledger_claim_and_unassign_run_the_native_protocol_once(tmp_path: Path, bare: Path) -> None:
    root = _repo(tmp_path, bare)
    made = _kit(root, "create", LEDGER, "--title", "A canonical claim")
    assert made.returncode == 0, made.stdout + made.stderr
    record = _report(made)["record"]

    claimed = _kit(root, "claim", LEDGER, record)
    released = _kit(root, "unassign", LEDGER, record)

    assert claimed.returncode == released.returncode == 0, (claimed.stdout, released.stdout)
    assert _report(claimed)["schema"] == "basicly.tracker.claim-vault.v1"
    assert _report(released)["schema"] == "basicly.tracker.unassign-vault.v1"
    assert _report(claimed)["record"] == _report(released)["record"] == record


@pytest.mark.parametrize(
    "form", [["claim", "--to", "teammate"], ["claim", "--take"], ["unassign", "--by", "other"]]
)
def test_ledger_only_holder_flags_refuse_without_changing_the_queue_and_name_the_native_form(
    tmp_path: Path, bare: Path, form: list[str]
) -> None:
    root = _repo(tmp_path, bare)
    made = _kit(root, "create", LEDGER, "--title", "A preserved claim")
    assert made.returncode == 0, made.stdout + made.stderr
    record = _report(made)["record"]
    queue = _clone(tmp_path, bare).folder / vault_queue.QUEUE_NAME
    assert queue.is_file()
    before = queue.read_bytes()

    done = _kit(root, form[0], LEDGER, record, *form[1:])

    assert done.returncode == 1, done.stdout + done.stderr
    refused = _report(done)["refused"]
    assert form[1] in refused and record in refused
    assert queue.read_bytes() == before
    remedy = shlex.split(refused.split("run `", 1)[1].split("`", 1)[0])
    parsed = cli.vault_arguments.FORMS[remedy[0]]().parse_args([*remedy, "--vault", "vault.git"])
    assert parsed.command == form[0] + "-vault" and parsed.record == record

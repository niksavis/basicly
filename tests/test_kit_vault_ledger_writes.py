from __future__ import annotations

import shlex
from pathlib import Path

import pytest

from tests.test_kit_vault_cli_wiring import (
    LEDGER,
    _clone,
    _environment,
    _hold_the_share_slot,
    _kit,
    _repo,
    _report,
    cli,
    vault_queue,
)
from tests.test_kit_vault_share import bare

__all__ = ["_environment", "bare"]

FORMS = {
    "child": ["--title", "child"],
    "update": ["--field", "priority=2"],
    "close": ["--reason", "done"],
    "review": ["--evidence", "{}"],
    "confirm": ["--evidence", "{}"],
    "comment": ["a useful note"],
    "dep": ["acme-other", "--type", "blocks"],
    "undep": ["acme-other", "--type", "blocks"],
    "delete": [],
    "assign": ["--to", "sam"],
    "claim": [],
    "unassign": [],
    "resolve": [],
    "acknowledge-loss": ["--sequence", "1", "--reason", "lost", "--evidence", "proof"],
}
REFUSED = frozenset({"assign", "resolve", "acknowledge-loss", "delete"})


def test_every_record_write_form_is_in_the_vault_contract_population() -> None:
    assert set(FORMS) == set(cli._WRITES)


@pytest.mark.parametrize("verb", sorted(REFUSED))
@pytest.mark.parametrize("pending", [False, True])
def test_an_unsupported_vault_ledger_write_refuses_without_changing_any_local_file(
    tmp_path: Path, bare: Path, verb: str, pending: bool
) -> None:
    root = _repo(tmp_path, bare)
    if pending:
        _hold_the_share_slot(tmp_path, bare)
    created = _kit(root, "create", LEDGER, "--title", "vault born")
    assert created.returncode == 0, created.stdout + created.stderr
    record = _report(created)["record"]
    clone = _clone(tmp_path, bare)
    queue = clone.folder / vault_queue.QUEUE_NAME
    before_queue = queue.read_bytes() if queue.exists() else b""
    before_ledger = {p.name: p.read_bytes() for p in (root / LEDGER).iterdir() if p.is_file()}

    done = _kit(root, verb, LEDGER, record, *FORMS[verb])

    assert done.returncode == 1, done.stdout + done.stderr
    refused = _report(done)["refused"]
    assert verb in refused and record in refused
    assert "ledger" in refused
    assert "\n" not in refused and str(tmp_path) not in refused
    assert (queue.read_bytes() if queue.exists() else b"") == before_queue
    assert {
        p.name: p.read_bytes() for p in (root / LEDGER).iterdir() if p.is_file()
    } == before_ledger
    remedy = refused.split("run `", 1)[1].split("`", 1)[0]
    argv = shlex.split(remedy)
    if verb == "delete":
        args = cli.arguments.parser().parse_args(argv)
        assert args.command == "close" and args.resolution == "cancelled"
        assert args.record == [record]
    else:
        args = cli.vault_arguments.FORMS[argv[0]]().parse_args([*argv, "--vault", "vault.git"])
        if verb == "assign":
            assert args.command == "claim-vault" and args.record == record
        else:
            assert args.command == "fsck-vault" and args.repair
    assert "nothing was written" in refused

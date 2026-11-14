from __future__ import annotations

from pathlib import Path

import pytest

from tests.test_kit_vault_write import (
    CONTEXT,
    _environment,
    _ledger_bytes,
    _queued,
    _seeded,
    bare,
    commands,
    events,
)

__all__ = ["_environment", "bare"]


@pytest.mark.parametrize("completed", [False, True], ids=["review", "confirm"])
def test_vault_evidence_refuses_redacted_commands_before_queuing(
    tmp_path: Path, bare: Path, completed: bool
) -> None:
    repo = _seeded(tmp_path, bare)
    commands.update(
        repo.ledger, repo.record, fields={"issue_type": "task", "size": "small"}, context=CONTEXT
    )
    check = {
        "criterion": "the seeded record keeps its criterion",
        "command": ["python3", "/".join(["", "tmp", "fixture", "check.py"])],
    }
    check.update(
        {"result": "The fixture preserved the criterion.", "exit_code": 0}
        if completed
        else {"expected": "The fixture preserves the criterion."}
    )
    before, queued = _ledger_bytes(repo.ledger), _queued(repo)
    with pytest.raises(events.LedgerError, match="redaction"):
        commands.record_process(
            repo.ledger, repo.record, {"checks": [check]}, completed=completed, redact=CONTEXT
        )
    assert _queued(repo) == queued
    assert _ledger_bytes(repo.ledger) == before

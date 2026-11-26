from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from tests.test_kit_vault_share import bare
from tests.test_kit_vault_write import _cli, _environment, _seeded, vault_claim, vault_write

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["_environment", "bare"]


@pytest.mark.parametrize(
    ("environ", "given", "expected"),
    [
        ({"BASICLY_TRACKER_SESSION": "t", "ORCH_SESSION_ID": "o"}, "flag", "flag"),
        ({"BASICLY_TRACKER_SESSION": "t", "ORCH_SESSION_ID": "o"}, None, "t"),
        ({"ORCH_SESSION_ID": "o", "CLAUDE_CODE_SESSION_ID": "c"}, None, "o"),
        ({"BASICLY_TRACKER_SESSION": "", "CLAUDE_CODE_SESSION_ID": "c"}, None, "c"),
        ({}, None, ""),
    ],
)
def test_the_session_is_the_flag_then_the_tracker_session_then_the_agent_session(
    environ: dict[str, str], given: str | None, expected: str
) -> None:
    assert vault_write.session_of(environ, given) == expected


def test_the_cli_ledger_claim_takes_the_session_flag_its_refusal_names(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = _seeded(tmp_path, bare)
    monkeypatch.setenv("BASICLY_TRACKER_SESSION", "")

    nameless = _cli(repo.root, "claim", ".basicly/ledger", repo.record)
    named = _cli(repo.root, "claim", ".basicly/ledger", repo.record, "--session", "s9")

    assert "--session" in nameless.stdout
    assert named.returncode == 0, named.stdout + named.stderr
    assert vault_claim.holder(repo.clone, repo.record)[1] == "s9"


@pytest.mark.parametrize("variable", ["CLAUDE_CODE_SESSION_ID", "ORCH_SESSION_ID"])
def test_the_fenced_cli_claim_takes_the_agent_session_when_no_tracker_session_is_set(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, variable: str
) -> None:
    repo = _seeded(tmp_path, bare)
    monkeypatch.delenv("BASICLY_TRACKER_SESSION", raising=False)
    monkeypatch.setenv(variable, "agent-7")

    done = _cli(repo.root, "claim", ".basicly/ledger", repo.record)

    assert done.returncode == 0, done.stdout + done.stderr
    assert vault_claim.holder(repo.clone, repo.record)[1] == "agent-7"

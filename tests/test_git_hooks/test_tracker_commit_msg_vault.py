from __future__ import annotations

import os
import subprocess  # nosec B404
import sys
from pathlib import Path

import pytest

from tests.test_kit_ledger_freeze import _migration, cli
from tests.test_kit_vault_share import bare

__all__ = ["bare"]

HOOK = Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "tracker-commit-msg.py"


def _commit_msg(project: Path, message: str) -> subprocess.CompletedProcess[str]:
    message_file = project.parent / "COMMIT_MSG"
    message_file.write_text(message + "\n", encoding="utf-8")
    return subprocess.run(  # nosec B603
        [sys.executable, str(HOOK), str(message_file)],
        cwd=project,
        env=dict(os.environ),
        capture_output=True,
        text=True,
        encoding="utf-8",
        timeout=120,
        check=False,
    )


def _git_project(project: Path) -> None:
    done = subprocess.run(  # nosec B603 B607
        ["git", "init", "-q"], cwd=project, capture_output=True, text=True, check=False
    )
    assert done.returncode == 0, done.stderr


def _freeze(ledger: Path, bare: Path, state: Path) -> None:
    code = cli.main([
        "migrate",
        str(ledger),
        "--to-vault",
        "--freeze",
        "--prefix",
        "demo",
        "--vault",
        str(bare),
        "--state",
        str(state),
    ])
    assert code == 0


@pytest.mark.parametrize("frozen", [False, True], ids=["local-ledger", "vault-frozen"])
def test_an_unknown_record_id_is_refused_before_and_after_a_vault_freeze(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch, frozen: bool
) -> None:
    ledger, state = _migration(tmp_path, bare, monkeypatch)
    project = ledger.parent.parent
    _git_project(project)
    if frozen:
        _freeze(ledger, bare, state)
        assert (project / ".basicly-tracker.toml").is_file()

    unknown = _commit_msg(project, "fix: use a missing record (demo-zzzzzz)")
    known = _commit_msg(project, "fix: use the record (demo-a1b2)")

    assert unknown.returncode == 1, unknown.stdout + unknown.stderr
    assert "unknown issue id: demo-zzzzzz" in unknown.stderr
    assert known.returncode == 0, known.stdout + known.stderr
    assert "Issue id reference is valid." in known.stdout

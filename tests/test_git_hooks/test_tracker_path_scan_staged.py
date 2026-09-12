from __future__ import annotations

import json
import subprocess
import sys
from pathlib import Path

SCRIPT = (
    Path(__file__).resolve().parents[2] / ".basicly" / "core" / "hooks" / "tracker-path-scan.py"
)
LEDGER = Path(".basicly") / "ledger" / "events-0001.jsonl"

FOREIGN_PATH = "/home/someoneelse/work/acme"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True)  # nosec B603 B607


def _ledger_only_repo(root: Path, *events: dict[str, object]) -> None:
    log = root / LEDGER
    log.parent.mkdir(parents=True)
    log.write_text("".join(json.dumps(event) + "\n" for event in events), encoding="utf-8")
    _git(root, "init", "-b", "main")
    _git(root, "config", "user.name", "Test")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "add", "-A")


def _run_hook(root: Path) -> subprocess.CompletedProcess[str]:
    return subprocess.run(  # nosec B603
        [sys.executable, str(SCRIPT)], cwd=root, capture_output=True, text=True, check=False
    )


def _event(record: str, payload: dict[str, object]) -> dict[str, object]:
    return {"id": f"e-{record}", "record": record, "kind": "created", "payload": payload}


def test_a_staged_ledger_event_with_a_machine_path_blocks_without_an_external_store(
    tmp_path: Path,
) -> None:
    _ledger_only_repo(
        tmp_path,
        _event("acme-1", {"source_repo_path": FOREIGN_PATH}),
        _event("acme-2", {"title": "a record with no location in it"}),
    )
    assert not (tmp_path / ".beads").exists()

    result = _run_hook(tmp_path)

    assert result.returncode == 1, result.stdout + result.stderr
    assert f"{LEDGER.as_posix()}:1: posix-home-path" in result.stderr
    assert ":2:" not in result.stderr


def test_a_clean_ledger_passes_and_a_machine_path_elsewhere_is_not_its_business(
    tmp_path: Path,
) -> None:
    _ledger_only_repo(tmp_path, _event("acme-2", {"title": "a record with no location in it"}))
    note = tmp_path / "docs" / "note.md"
    note.parent.mkdir()
    note.write_text(f"A document may legitimately name {FOREIGN_PATH}.\n", encoding="utf-8")
    _git(tmp_path, "add", "-A")

    result = _run_hook(tmp_path)

    assert result.returncode == 0, result.stdout + result.stderr

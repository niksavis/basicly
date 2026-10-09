from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from basicly import cli
from tests.flipped_tracker import flipped_repo, seed_records
from tests.kit_deployment_helpers import KIT_RELATIVE, REPO_ROOT, _load

if TYPE_CHECKING:
    from pathlib import Path

kit_cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "tracker_list_statuses_kit_cli")
SEEDED = [
    {"id": "basicly-aaa", "status": "open", "title": "open work"},
    {"id": "basicly-bbb", "status": "in_progress", "title": "held work"},
    {"id": "basicly-ccc", "status": "closed", "title": "done work"},
]


def _repo(tmp_path: Path) -> Path:
    repo = flipped_repo(tmp_path)
    seed_records(repo, SEEDED)
    return repo


def _engine(repo: Path, monkeypatch: pytest.MonkeyPatch, *args: str) -> int:
    monkeypatch.chdir(repo)
    return cli.main(["tracker", "list", *args])


def _kit(repo: Path, *args: str) -> int:
    return kit_cli.main(["list", str(repo / ".basicly" / "ledger"), *args])


@pytest.mark.parametrize("surface", ["engine", "kit"])
def test_list_takes_several_statuses(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    surface: str,
) -> None:
    repo = _repo(tmp_path)
    args = ("--status", "open", "--status", "in_progress")
    code = _engine(repo, monkeypatch, *args) if surface == "engine" else _kit(repo, *args)
    listed = json.loads(capsys.readouterr().out)
    assert code == 0
    assert sorted(record["record"] for record in listed["records"]) == [
        "basicly-aaa",
        "basicly-bbb",
    ]


@pytest.mark.parametrize("surface", ["engine", "kit"])
def test_list_refuses_an_unknown_status(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    surface: str,
) -> None:
    repo = _repo(tmp_path)
    code = (
        _engine(repo, monkeypatch, "--status", "bogus")
        if surface == "engine"
        else _kit(repo, "--status", "bogus")
    )
    captured = capsys.readouterr()
    assert code != 0
    assert "status 'bogus' is not one of open, in_progress, blocked, deferred, closed." in (
        captured.out + captured.err
    )

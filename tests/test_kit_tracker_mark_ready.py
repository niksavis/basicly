from __future__ import annotations

import importlib.util
import json
import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.tracker_process_fixture import recorded_review

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"
TRIGGER = "When a built story waits, I want its ready time recorded, so I can deliver the oldest."
SHAPED = ("--description", TRIGGER, "--acceptance", "- it is seen", "--requirements", "- stdlib")


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_mark_ready")


def _run(capsys: pytest.CaptureFixture[str], *argv: str) -> tuple[int, dict[str, Any]]:
    capsys.readouterr()
    code = cli.main(list(argv))
    return code, json.loads(capsys.readouterr().out)


@pytest.fixture(autouse=True)
def _no_developer_git_config(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    home = tmp_path / "developer-home"
    home.mkdir()
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    for variable in ("XDG_CONFIG_HOME", "BASICLY_HOLDER", "GIT_AUTHOR_NAME", "GIT_COMMITTER_NAME"):
        monkeypatch.delenv(variable, raising=False)
    monkeypatch.chdir(tmp_path)


def _story(ledger: Path, capsys: pytest.CaptureFixture[str], title: str = "s") -> str:
    _, made = _run(capsys, "create", str(ledger), "--prefix", "acme", "--title", title, *SHAPED)
    recorded_review(cli, ledger, made["record"])
    return made["record"]


@pytest.fixture
def claimed(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    ledger = tmp_path / "ledger"
    record = _story(ledger, capsys)
    assert _run(capsys, "claim", str(ledger), record, "--to", "alex")[0] == cli.EXIT_OK
    return ledger, record


def test_mark_ready_appends_the_mark_and_list_gives_its_utc_event_time(
    claimed: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = claimed

    code, _ = _run(capsys, "mark-ready", str(ledger), record, "--to", "alex")

    assert code == cli.EXIT_OK
    _, shown = _run(capsys, "show", str(ledger), record)
    assert shown["fields"]["ready_for_delivery"] == "ready"
    _, listed = _run(capsys, "list", str(ledger), "--ready-for-delivery")
    marked = datetime.fromisoformat(listed["records"][0]["ready_for_delivery"])
    assert marked.utcoffset() == timedelta(0)


@pytest.mark.parametrize(
    ("setup", "argv", "refusal"),
    [
        ("open", ("--to", "alex"), "is open; only an in_progress record waits"),
        ("claimed", ("--to", "sam"), "is held by alex, not by sam"),
        ("marked", ("--to", "alex"), "pass --again to mark it again"),
        ("claimed", ("--to", "alex", "--clear"), "carries no ready mark"),
    ],
)
def test_mark_ready_refuses_by_name(
    tmp_path: Path,
    capsys: pytest.CaptureFixture[str],
    setup: str,
    argv: tuple[str, ...],
    refusal: str,
) -> None:
    ledger = tmp_path / "ledger"
    record = _story(ledger, capsys)
    if setup != "open":
        _run(capsys, "claim", str(ledger), record, "--to", "alex")
    if setup == "marked":
        _run(capsys, "mark-ready", str(ledger), record, "--to", "alex")

    code, report = _run(capsys, "mark-ready", str(ledger), record, *argv)

    assert code != cli.EXIT_OK
    assert refusal in json.dumps(report)


def test_again_moves_the_mark_and_clear_removes_it(
    claimed: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = claimed
    _run(capsys, "mark-ready", str(ledger), record, "--to", "alex")
    first = _run(capsys, "list", str(ledger), "--ready-for-delivery")[1]["records"][0]

    assert _run(capsys, "mark-ready", str(ledger), record, "--to", "alex", "--again")[0] == 0
    again = _run(capsys, "list", str(ledger), "--ready-for-delivery")[1]["records"][0]
    assert again["ready_for_delivery"] >= first["ready_for_delivery"]

    assert _run(capsys, "mark-ready", str(ledger), record, "--to", "alex", "--clear")[0] == 0
    assert not _run(capsys, "show", str(ledger), record)[1]["fields"].get("ready_for_delivery")
    assert _run(capsys, "list", str(ledger), "--ready-for-delivery")[1]["records"] == []


def test_show_gives_the_mark_and_list_puts_the_oldest_ready_time_first(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    ledger = tmp_path / "ledger"
    older, newer, unmarked = (_story(ledger, capsys, title) for title in ("a", "b", "c"))
    for record in (older, newer, unmarked):
        _run(capsys, "claim", str(ledger), record, "--to", "alex")
    _run(capsys, "mark-ready", str(ledger), older, "--to", "alex")
    _run(capsys, "mark-ready", str(ledger), newer, "--to", "alex")

    _, listed = _run(capsys, "list", str(ledger), "--ready-for-delivery")

    assert [row["record"] for row in listed["records"]] == [older, newer]
    first, second = (row["ready_for_delivery"] for row in listed["records"])
    assert first <= second
    _, shown = _run(capsys, "show", str(ledger), older)
    assert shown["fields"]["ready_for_delivery"] == "ready"

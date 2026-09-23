from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import owned_store

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


cli = _load(KIT_DIR / "cli.py", "tracker_cli_values")


def _report(capsys: pytest.CaptureFixture[str]) -> dict[str, Any]:
    return json.loads(capsys.readouterr().out)


@pytest.fixture
def made(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> tuple[Path, str]:
    ledger = tmp_path / "ledger"
    cli.main(["create", str(ledger), "--prefix", "acme", "--title", "a"])
    return ledger, _report(capsys)["record"]


@pytest.mark.parametrize(
    ("argv", "named"),
    [
        (["update", "--status", "done"], "status 'done' is not one of"),
        (["update", "--status", "in-progress"], "did you mean 'in_progress'"),
        (["update", "--field", "priority=9"], "priority 9 is not a whole number"),
        (["update", "--field", "priority=high"], "priority 'high'"),
        (["update", "--field", "priority=true"], "priority True"),
        (["close"], "needs a reason"),
        (["close", "--reason", "  "], "needs a reason"),
    ],
)
def test_a_wrong_value_is_refused_by_name_and_writes_nothing(
    made: tuple[Path, str], capsys: pytest.CaptureFixture[str], argv: list[str], named: str
) -> None:
    ledger, record = made
    before = cli.events.read_events(ledger)[0]

    assert cli.main([argv[0], str(ledger), record, *argv[1:]]) == cli.EXIT_REFUSED

    assert named in _report(capsys)["refused"]
    assert cli.events.read_events(ledger)[0] == before


def test_a_create_at_a_wrong_priority_is_refused(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    argv = ["create", str(tmp_path / "l"), "--prefix", "acme", "--field", "priority=7"]

    assert cli.main(argv) == cli.EXIT_REFUSED
    assert "priority 7" in _report(capsys)["refused"]


def test_a_status_the_record_held_before_is_recorded_again(
    made: tuple[Path, str], capsys: pytest.CaptureFixture[str]
) -> None:
    ledger, record = made
    cli.main(["update", str(ledger), record, "--status", "in_progress"])
    capsys.readouterr()

    assert cli.main(["update", str(ledger), record, "--status", "open"]) == cli.EXIT_OK

    assert _report(capsys)["appended"] is True
    cli.main(["show", str(ledger), record])
    assert _report(capsys)["status"] == "open"


def test_a_kit_older_than_the_engine_names_the_fix(tmp_path: Path) -> None:
    (tmp_path / owned_store.KIT_TRACKER_DIR).mkdir(parents=True)

    with pytest.raises(owned_store.TrackerDivergenceError, match="run `basicly install`"):
        owned_store.kit(tmp_path, "writers")

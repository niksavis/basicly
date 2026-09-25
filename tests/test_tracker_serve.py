from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import owned_store, redact, tracker_query

if TYPE_CHECKING:
    import pytest

STAND_IN = """
import json
import sys


def run(args, redact):
    reported = {"directory": args.directory, "redact": redact.__name__}
    sys.stdout.write(json.dumps({**reported, "relaunch": args.relaunch}))
    return 7
"""


def _args() -> argparse.Namespace:
    parser = argparse.ArgumentParser()
    tracker_query.add_parsers(parser.add_subparsers(dest="command"))
    return parser.parse_args(["serve", "--port", "0"])


def test_serve_runs_the_board_kit_with_the_engine_redaction(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    board = tmp_path / owned_store.KIT_TRACKER_DIR.parent / tracker_query.BOARD_KIT
    board.mkdir(parents=True)
    (board / tracker_query.BOARD_ENTRY).write_text(STAND_IN, encoding="utf-8")
    monkeypatch.chdir(tmp_path)

    assert tracker_query.cmd_serve(_args()) == 7

    reported = json.loads(capsys.readouterr().out)
    assert reported["redact"] == redact.redact_committed.__name__
    assert Path(reported["directory"]) == owned_store.ledger_dir(tmp_path)
    assert reported["relaunch"] == tracker_query.SERVE_COMMAND


def test_serve_without_the_board_kit_names_where_it_looked(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)

    assert tracker_query.cmd_serve(_args()) == 1

    assert "the board kit is not installed" in capsys.readouterr().err

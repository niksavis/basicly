from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from typing import Any

import pytest

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit" / "tracker"


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


writers = _load(KIT_DIR / "writers.py", "tracker_writers_under_test")


@pytest.mark.parametrize(
    ("environ", "expected"),
    [
        ({}, "operator"),
        ({"BR_AGENT_NAME": "codex"}, "agent:codex"),
        ({"AI_AGENT": "claude-code_2-1-280_agent"}, "agent:claude-code_2-1-280_agent"),
        ({"CLAUDECODE": "1"}, "agent:claude-code"),
        ({"CLAUDECODE": "0"}, "operator"),
        ({"BR_AGENT_NAME": "codex", "AI_AGENT": "other", "CLAUDECODE": "1"}, "agent:codex"),
        ({"BR_AGENT_NAME": "   "}, "operator"),
    ],
)
def test_the_writer_class_follows_the_first_marker_present(
    environ: dict[str, str], expected: str
) -> None:
    assert writers.writer_class(environ) == expected


def test_a_marker_cannot_carry_a_path_a_secret_or_a_newline_onto_the_ledger() -> None:
    leaked = writers.writer_class({"AI_AGENT": "/home/someone/bin agent\nAPI_KEY=abc"})

    assert leaked.startswith("agent:")
    assert "/" not in leaked and "\n" not in leaked and " " not in leaked and "=" not in leaked


def test_a_long_marker_is_capped() -> None:
    capped = writers.writer_class({"AI_AGENT": "a" * 500})

    assert capped == "agent:" + "a" * writers.MAX_NAME_CHARS


def test_a_standalone_write_records_the_writer_class(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    cli = _load(KIT_DIR / "cli.py", "tracker_cli_writers")
    for name in ("BR_AGENT_NAME", "AI_AGENT", "CLAUDECODE"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setenv("AI_AGENT", "codex")
    ledger = tmp_path / "ledger"

    assert cli.main(["create", str(ledger), "--prefix", "acme", "--title", "a"]) == cli.EXIT_OK
    capsys.readouterr()

    found, _ = cli.events.read_events(ledger)
    assert {event.actor for event in found} == {"agent:codex"}

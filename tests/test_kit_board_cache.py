from __future__ import annotations

import importlib.util
import operator
import os
import shutil
import subprocess
import sys
import threading
from collections import Counter
from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest

from tests.kit_board_client import Client

KIT_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "kit"
TRIGGER = "When a card opens, I want it at once, so I can read it while the board loads."
PAGE_READS = (
    "/api/v1",
    "/api/v1/ready?limit=5000",
    "/api/v1/blocked",
    "/api/v1/refine",
    "/api/v1/records?status=open",
    "/api/v1/records?status=in_progress",
    "/api/v1/records?status=blocked",
    "/api/v1/records?status=deferred",
)


def _load(path: Path, name: str) -> Any:
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


server = _load(KIT_DIR / "board" / "server.py", "basicly_tracker_kit_server")
cli = server.tracker_cli()
events = cli.events


def _counted(monkeypatch: pytest.MonkeyPatch, name: str, tally: Counter[str]) -> None:
    original: Callable[..., Any] = getattr(events, name)

    def counting(*args: Any, **kwargs: Any) -> Any:
        tally[name] += 1
        return original(*args, **kwargs)

    monkeypatch.setattr(events, name, counting)


def _create(directory: Path, title: str) -> str:
    argv = ["create", str(directory), "--prefix", "demo", "--title", title]
    args = cli.arguments.parser().parse_args([
        *argv,
        "--description",
        TRIGGER,
        "--acceptance",
        "- a",
    ])
    code, report = cli.invoke(args)
    assert code == cli.EXIT_OK
    return report["record"]


@pytest.fixture
def made(tmp_path: Path) -> tuple[Path, str]:
    directory = tmp_path / "made"
    record = _create(directory, "seed")
    other = _create(directory, "busy")
    notes = range(events.MEMO_MIN_EVENTS)
    events.append(directory, [events.Draft(other, "comment", {"text": f"n{n}"}) for n in notes])
    served = tmp_path / "served"
    shutil.copytree(directory, served)
    return served, record


@pytest.fixture
def ledger(made: tuple[Path, str]) -> Path:
    return made[0]


@pytest.fixture
def record(made: tuple[Path, str]) -> str:
    return made[1]


@pytest.fixture
def client(ledger: Path, capsys: pytest.CaptureFixture[str]) -> Iterator[Client]:
    capsys.readouterr()
    served = server.make_server(ledger, "127.0.0.1", 0)
    thread = threading.Thread(target=served.serve_forever, daemon=True)
    thread.start()
    try:
        yield Client(served.server_address[1])
    finally:
        served.shutdown()
        served.server_close()


def _page(client: Client, record: str) -> list[dict[str, Any]]:
    paths = (*PAGE_READS, f"/api/v1/records/{record}", f"/api/v1/records/{record}/dor")
    answers = [client.call("GET", path) for path in paths]
    assert {status for status, _ in answers} == {200}
    return [report for _, report in answers]


def _comments(client: Client, record: str) -> list[str]:
    status, shown = client.call("GET", f"/api/v1/records/{record}")
    assert status == 200
    return [entry["text"] for entry in shown["comment_log"]]


def test_an_unchanged_ledger_is_parsed_once_and_a_write_is_seen(
    client: Client, ledger: Path, record: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    tally: Counter[str] = Counter()
    _counted(monkeypatch, "_parse_log", tally)
    _counted(monkeypatch, "_folded", tally)

    first = _page(client, record)
    parsed_first = dict(tally)
    again = _page(client, record)
    parsed_again = dict(tally)

    assert parsed_first == {"_parse_log": len(events.ledger_paths(ledger)), "_folded": 1}
    assert parsed_again == parsed_first
    assert again == first

    status, _ = client.call(
        "POST", f"/api/v1/records/{record}/comments", {"text": "through the server"}
    )
    assert status == 200
    assert _comments(client, record) == ["through the server"]

    script = [sys.executable, str(KIT_DIR / "tracker" / "cli.py"), "comment"]
    done = subprocess.run(
        [*script, str(ledger), record, "from another process"],
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert _comments(client, record) == ["through the server", "from another process"]
    assert tally["_parse_log"] > parsed_again["_parse_log"]


def test_a_same_size_rewrite_that_keeps_the_stamp_is_still_seen(
    client: Client, ledger: Path, record: str
) -> None:
    status, _ = client.call("POST", f"/api/v1/records/{record}/comments", {"text": "alpha note"})
    assert status == 200
    assert _comments(client, record) == ["alpha note"]
    log = next(path for path in events.ledger_paths(ledger) if b"alpha note" in path.read_bytes())
    held = log.stat()

    with log.open("r+b") as stream:
        rewritten = stream.read().replace(b"alpha note", b"omega note")
        stream.seek(0)
        stream.write(rewritten)
    os.utime(log, ns=(held.st_atime_ns, held.st_mtime_ns))
    after = log.stat()

    assert (after.st_ino, after.st_size, after.st_mtime_ns) == (
        held.st_ino,
        held.st_size,
        held.st_mtime_ns,
    )
    assert _comments(client, record) == ["omega note"]


def test_a_caller_that_changes_what_it_read_cannot_change_the_next_read(
    ledger: Path, record: str
) -> None:
    found = events.read_events(ledger)[0]
    folded = events.fold(found)
    title = folded.records[record].fields["title"]

    found.clear()
    folded.records[record].fields["title"] = "changed by a caller"
    folded.records[record].comments.append("added by a caller")
    folded.records.clear()
    events.canonical_order(events.read_events(ledger)[0]).clear()

    again = events.read_events(ledger)[0]
    refolded = events.fold(again)
    assert again
    assert refolded.records[record].fields["title"] == title
    assert refolded.records[record].comments == []
    assert events.canonical_order(again)
    with pytest.raises(TypeError):
        operator.setitem(events.fields_by_record(again)[record], "title", "changed")

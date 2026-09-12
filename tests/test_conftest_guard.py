from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from basicly import owned_write, tracker
from tests import flipped_tracker, ledger_guard

NODE = "tests/test_conftest_guard.py::test_the_writer_is_named"

RECORD = "vkh-51"

PROBE_LINE = b'{"probe": "this is not an event"}\n'


def temp_copy(tmp_path: Path, live: Path) -> tuple[Path, Path]:

    root = tmp_path / "ledger"
    root.mkdir()
    logs = sorted(live.glob(ledger_guard.LOG_GLOB))
    copy = root / (logs[-1].name if logs else "events-0001.jsonl")
    if logs:
        with logs[-1].open("rb") as stream:
            copy.write_bytes(stream.readline())
    else:
        copy.write_bytes(b'{"stand-in": "no live log in this checkout"}\n')
    return root, copy


@pytest.fixture
def live_copy(tmp_path: Path, request: pytest.FixtureRequest) -> tuple[Path, Path]:
    return temp_copy(tmp_path, Path(request.config.rootpath) / ".basicly" / "ledger")


APPEND_SCRIPT = (
    "import pathlib, sys\n"
    "with pathlib.Path(sys.argv[1]).open('ab') as log:\n"
    "    log.write(sys.argv[2].encode())\n"
)


def append_from_another_process(path: Path, line: bytes) -> None:

    subprocess.run(
        [sys.executable, "-c", APPEND_SCRIPT, str(path), line.decode()],
        check=True,
    )


def test_a_test_that_appends_to_the_watched_store_is_named_and_nothing_is_undone(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    before = log.read_bytes()

    with ledger_guard.watching(root, NODE) as watch, log.open("ab") as stream:
        stream.write(PROBE_LINE)

    with pytest.raises(pytest.fail.Exception) as blame:
        ledger_guard.report(watch)
    assert NODE in str(blame.value)
    assert log.name in str(blame.value)
    assert log.read_bytes() == before + PROBE_LINE


def test_a_write_from_another_process_is_reported_and_not_blamed(
    live_copy: tuple[Path, Path],
) -> None:
    root, log = live_copy
    before = log.read_bytes()

    with ledger_guard.watching(root, NODE) as watch:
        append_from_another_process(log, PROBE_LINE)

    assert watch.written == ()
    assert watch.unexplained == (str(log),)
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        ledger_guard.report(watch)
    assert [warned.category for warned in caught] == [ledger_guard.LedgerChangedWarning]
    notice = str(caught[0].message)
    assert "no write from this process explains it" in notice
    assert "No test is blamed" in notice
    assert log.read_bytes() == before + PROBE_LINE


def test_a_log_another_process_created_is_reported_and_left_alone(
    live_copy: tuple[Path, Path],
) -> None:

    root, _ = live_copy
    appeared = root / "events-0002.jsonl"

    with ledger_guard.watching(root, NODE) as watch:
        append_from_another_process(appeared, PROBE_LINE)

    assert watch.written == ()
    assert watch.unexplained == (str(appeared),)
    with warnings.catch_warnings(record=True):
        warnings.simplefilter("always")
        ledger_guard.report(watch)
    assert appeared.read_bytes() == PROBE_LINE


def test_reading_the_watched_store_is_not_a_write(live_copy: tuple[Path, Path]) -> None:

    root, log = live_copy

    with ledger_guard.watching(root, NODE) as watch:
        assert log.read_bytes()

    assert watch.written == ()
    assert watch.unexplained == ()
    ledger_guard.report(watch)


def test_the_original_incident_shape_still_reaches_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, RECORD, title="the incident shape")
    monkeypatch.chdir(repo)

    with ledger_guard.watching(tracker.ledger_dir(repo), NODE) as watch:
        owned_write.append(Path(), ["update", RECORD, "-t", "bug"])

    assert len(flipped_tracker.ledger_events(repo)) > 1
    logs = sorted(tracker.ledger_dir(repo).glob(ledger_guard.LOG_GLOB))
    assert [Path(name).name for name in watch.written if name.endswith(".jsonl")] == [logs[0].name]
    with pytest.raises(pytest.fail.Exception) as blame:
        ledger_guard.report(watch)
    assert NODE in str(blame.value)

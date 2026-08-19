"""What the live-ledger guard blames, and what it refuses to touch (basicly-vkh0.51).

The guard's detection half was never in doubt; its *reaction* was. It restored the old
bytes of an append-only log and failed the test in flight, so a tracker write issued by
another process while the suite ran was undone and charged to whichever test happened to
be running. These tests bind the four answers that replace it: a write from this process
is named, a write from another process is reported but not blamed, nothing is ever put
back, and the shape of the original incident — a cwd left at the real repository, the
engine's own seam reached with ``Path()`` as the repo root — still lands on the first one.

Every case runs against a temporary copy of the watched store, never the live one.
"""

from __future__ import annotations

import subprocess
import sys
import warnings
from pathlib import Path

import pytest

from basicly import owned_write, tracker
from tests import flipped_tracker, ledger_guard

# The node id under test, spelled out rather than taken from `request`, so the assertion
# that the blame message names the writer cannot pass by naming the wrong test.
NODE = "tests/test_conftest_guard.py::test_the_writer_is_named"

RECORD = "vkh-51"

PROBE_LINE = b'{"probe": "this is not an event"}\n'


def temp_copy(tmp_path: Path, live: Path) -> tuple[Path, Path]:
    """A temporary ledger directory holding a copy of *live*'s newest event log, and it.

    The head of the real artifact rather than a synthetic file, so the guard is exercised
    against the bytes it actually watches; the head alone, because the live log is ~6 MB
    and none of these assertions read past the first line. A checkout with no log yet —
    a fresh clone before its first write — gets a one-line stand-in.
    """
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
    """:func:`temp_copy` of this checkout's own ledger, found the way pytest finds it."""
    return temp_copy(tmp_path, Path(request.config.rootpath) / ".basicly" / "ledger")


# What the spawned writer runs. A separate interpreter rather than a thread, because a
# thread's opens raise the audit event in *this* process and would be attributed.
APPEND_SCRIPT = (
    "import pathlib, sys\n"
    "with pathlib.Path(sys.argv[1]).open('ab') as log:\n"
    "    log.write(sys.argv[2].encode())\n"
)


def append_from_another_process(path: Path, line: bytes) -> None:
    """Append *line* to *path* from a process this one only spawns.

    A real second process, because that is the whole discriminator: the audit hook sees
    opens in *this* interpreter, so a write it cannot see must be one it cannot blame.
    """
    subprocess.run(
        [sys.executable, "-c", APPEND_SCRIPT, str(path), line.decode()],
        check=True,
    )


def test_a_test_that_appends_to_the_watched_store_is_named_and_nothing_is_undone(
    live_copy: tuple[Path, Path],
) -> None:
    """The first acceptance criterion: the writer is named and its bytes stay written."""
    root, log = live_copy
    before = log.read_bytes()

    with ledger_guard.watching(root, NODE) as watch, log.open("ab") as stream:
        stream.write(PROBE_LINE)

    with pytest.raises(pytest.fail.Exception) as blame:
        ledger_guard.report(watch)
    assert NODE in str(blame.value)
    assert log.name in str(blame.value)
    # The point of the bead: the guard reports, and the log still holds what the writer
    # wrote. Restoring it would be an edit to a file that is supposed to have none.
    assert log.read_bytes() == before + PROBE_LINE


def test_a_write_from_another_process_is_reported_and_not_blamed(
    live_copy: tuple[Path, Path],
) -> None:
    """The second and third criteria: no failure, no restore, and it says what it saw."""
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
    """A log that appeared is the same question as one that grew.

    The old guard answered it by deleting the file, which is the same edit to an
    append-only store as the restore, made against a writer it never identified.
    """
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
    """The control for the two above: attribution keys on the mode, not on the path.

    Without this, a hook that recorded every ``open`` would pass both of them by blaming
    the guard's own snapshot read.
    """
    root, log = live_copy

    with ledger_guard.watching(root, NODE) as watch:
        assert log.read_bytes()

    assert watch.written == ()
    assert watch.unexplained == ()
    ledger_guard.report(watch)


def test_the_original_incident_shape_still_reaches_the_guard(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The fourth criterion, as basicly-e2mz.43 recorded it.

    ``Path()`` as the repo root plus a cwd at the repository under test is how the four
    events landed in the committed log: the engine's own write seam resolves the ledger
    relative to the cwd, and a kit loaded by path does the append. Nothing is patched here
    — the write really happens, into a ledger of this test's own.
    """
    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, RECORD, title="the incident shape")
    monkeypatch.chdir(repo)

    with ledger_guard.watching(tracker.ledger_dir(repo), NODE) as watch:
        owned_write.append(Path(), ["update", RECORD, "-t", "bug"])

    assert len(flipped_tracker.ledger_events(repo)) > 1
    # The event log itself, not merely something under the root: the kit takes the ledger
    # lock through `os.open` before it appends, so an attribution that saw only the lock
    # file would satisfy a check for "some path was written" while missing the write.
    logs = sorted(tracker.ledger_dir(repo).glob(ledger_guard.LOG_GLOB))
    assert [Path(name).name for name in watch.written if name.endswith(".jsonl")] == [logs[0].name]
    with pytest.raises(pytest.fail.Exception) as blame:
        ledger_guard.report(watch)
    assert NODE in str(blame.value)

from __future__ import annotations

import importlib.util
import io
import json
import os
import subprocess
import sys
import tokenize
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).parent.parent
EVENTS_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker" / "events.py"
MODULE_NAME = "tracker_events"

RECORD = "basicly-aa11"
CLOCK = 1_000_000_000.0


def _kit() -> ModuleType:

    cached = sys.modules.get(MODULE_NAME)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(MODULE_NAME, EVENTS_SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE_NAME] = module
    spec.loader.exec_module(module)
    return module


events = _kit()

_DYING_CHILD = """
import importlib.util
import os
import sys
from pathlib import Path

spec = importlib.util.spec_from_file_location("tracker_events", sys.argv[1])
module = importlib.util.module_from_spec(spec)
sys.modules["tracker_events"] = module
spec.loader.exec_module(module)


class Abandoned(list):
    def __iter__(self):
        for line in list.__iter__(self):
            yield line
        os._exit(9)


written = module._append_lines


def dying(path, lines):
    return written(path, Abandoned(lines))


module._append_lines = dying
module.append(
    Path(sys.argv[2]),
    [
        module.Draft(record="basicly-aa11", kind="note", payload={"text": sys.argv[4] % index})
        for index in range(int(sys.argv[3]))
    ],
    clock=lambda: 1_000_000_000.0,
)
print("the batch returned, so the process never died")
"""

PAYLOAD = "%06d" + "e" * 240


@pytest.fixture(name="dying_append")
def fixture_dying_append(tmp_path: Path) -> Callable[[int], tuple[bytes, Path]]:
    script = tmp_path / "dying_child.py"
    script.write_text(_DYING_CHILD, encoding="utf-8")

    def run(drafts: int) -> tuple[bytes, Path]:
        ledger = tmp_path / f"ledger-{drafts}"
        done = subprocess.run(
            [sys.executable, str(script), str(EVENTS_SOURCE), str(ledger), str(drafts), PAYLOAD],
            capture_output=True,
            text=True,
            check=False,
        )
        assert done.returncode == 9, f"the child did not die mid-batch: {done.stdout}{done.stderr}"
        log = ledger / events.INITIAL_LOG_NAME
        return log.read_bytes() if log.exists() else b"", ledger

    return run


def _one_line_bytes() -> int:

    event = events.Event(
        id="basicly-aa11#ev-0123456789",
        record=RECORD,
        seq=1,
        kind="note",
        actor="",
        ts="2001-09-09T01:46:40Z",
        payload={"text": PAYLOAD % 0},
        totals=events.Totals(events=1),
    )
    return len(events.to_json(event).encode("utf-8")) + 1


def test_a_durable_append_reports_success_without_an_fsync(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    calls: list[str] = []
    real_fsync = os.fsync
    monkeypatch.setattr(os, "fsync", lambda fd: calls.append("fsync") or real_fsync(fd))
    if (real_fdatasync := getattr(os, "fdatasync", None)) is not None:
        monkeypatch.setattr(
            os, "fdatasync", lambda fd: calls.append("fdatasync") or real_fdatasync(fd)
        )

    written = events.append(
        tmp_path,
        [events.Draft(record=RECORD, kind="note", payload={"text": f"n{i}"}) for i in range(5)],
        clock=lambda: CLOCK,
    )

    assert len(written) == 5
    assert calls == [], f"the append path now syncs: {calls}"
    handle = os.open(str(tmp_path / events.INITIAL_LOG_NAME), os.O_RDWR)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    assert calls == ["fsync"], "the spy counts nothing, so the zero above meant nothing"


def test_a_durable_write_asks_the_platform_for_no_sync_at_all() -> None:

    with EVENTS_SOURCE.open("rb") as handle:
        code = [
            token.string
            for token in tokenize.tokenize(handle.readline)
            if token.type not in (tokenize.COMMENT, tokenize.STRING)
        ]

    for name in ("fsync", "fdatasync", "O_SYNC", "O_DSYNC", "flush"):
        assert name not in code, (
            f"{name} is on the write path now, so `work-tracker.md` §4.4 changed"
        )
    assert "open" in code, "the positive control: the append is written through open()"


def test_a_durable_batch_under_one_buffer_chunk_is_all_or_nothing(
    dying_append: Callable[[int], tuple[bytes, Path]],
) -> None:

    assert events.BUFFER_CHUNK_BYTES <= io.DEFAULT_BUFFER_SIZE, (
        f"the stream buffers {io.DEFAULT_BUFFER_SIZE} bytes, under the floor the cap is sized on"
    )
    drafts = events.BUFFER_CHUNK_BYTES // _one_line_bytes() // 2
    assert drafts >= 2, "the batch has to be more than one line for the claim to mean anything"

    residue, ledger = dying_append(drafts)

    assert residue == b"", f"{len(residue)} bytes of a sub-chunk batch reached the file"
    found, quarantined = events.read_events(ledger)
    assert (found, quarantined) == ([], [])


def test_a_durable_batch_over_one_buffer_chunk_loses_a_whole_line_suffix(
    dying_append: Callable[[int], tuple[bytes, Path]],
) -> None:

    drafts = io.DEFAULT_BUFFER_SIZE * 7 // _one_line_bytes()

    residue, ledger = dying_append(drafts)

    assert residue, "nothing reached the file, so this batch never crossed the buffer"
    assert residue.endswith(b"\n"), "the cut fell inside a line, so a batch can tear"
    found, quarantined = events.read_events(ledger)
    assert quarantined == [], quarantined
    assert 0 < len(found) < drafts, "every line landed, so there is no bound measured here"
    assert [event.seq for event in found] == list(range(1, len(found) + 1))
    folded = events.fold(found)
    assert folded.mismatched_totals == []
    assert folded.forked == []
    assert folded.records[RECORD].totals.events == len(found)


def test_a_durable_log_reports_a_lost_interior_line_as_a_totals_disagreement(
    tmp_path: Path,
) -> None:

    batch = [events.Draft(record=RECORD, kind="note", payload={"text": f"n{i}"}) for i in range(3)]
    events.append(tmp_path, batch, clock=lambda: CLOCK)
    log = tmp_path / events.INITIAL_LOG_NAME
    lines = log.read_text(encoding="utf-8").splitlines()
    assert events.fold(events.read_events(tmp_path)[0]).mismatched_totals == [], (
        "the control: the log has to agree with itself before a line is taken out of it"
    )
    above_the_hole = json.loads(lines[2])

    log.write_text("\n".join([lines[0], lines[2]]) + "\n", encoding="utf-8")
    after = events.append(
        tmp_path,
        [events.Draft(record=RECORD, kind="note", payload={"text": "restating"})],
        clock=lambda: CLOCK,
    )

    found, quarantined = events.read_events(tmp_path)
    assert quarantined == [], "a hole leaves no unparseable line, which is why it is silent"
    assert [event.seq for event in found] == [1, 3, 4]
    folded = events.fold(found)
    assert folded.mismatched_totals == [above_the_hole["id"]]
    assert above_the_hole["totals"]["events"] == 3
    assert after[0].totals.events == 3, "the writer folded rather than reading the tail"
    assert folded.forked == []
    assert folded.records[RECORD].max_seq == 4

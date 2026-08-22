"""What the append path's durability actually is, measured (basicly-mbkqxi).

`events.py` states the choice — "No ``fsync``: the push is the durability boundary
(`work-tracker.md` §4.4)" — and nothing exercised it, so the bound it leaves behind was a claim.
These
tests measure it, because the bound is what decides whether a lost line could have come
from this path:

- **No `fsync`, and no `O_SYNC` either.** Asserted twice, once by counting the calls a
  whole append makes with a positive control on the counter, once off the source, because
  a behavioural count only covers the path it walked.
- **A batch under one buffer chunk is all-or-nothing against a process death**, and a
  batch over one loses a **suffix of whole lines**. So the residue of an interrupted
  append is a *shorter* ledger, never a holed one: 1,890 of 2,016 events on this platform,
  869,076 bytes ending in a newline, with nothing quarantined and no sequence hole.
- **An interior line that goes missing after it was written is detected**, by the carried
  totals disagreeing with the fold rather than by the sequence hole. That is the
  regression test for basicly-vkh0.30, whose ledger holds one such hole: the event above
  it carries ``events`` one higher than a fold of the surviving lines can reach, and that
  is the only reason the loss was ever visible.

The measurement that made this a finding rather than a fix: the surviving lines either
side of `basicly-vkh0.30`'s hole are adjacent in the file and 525 microseconds apart,
while one `append()` over that ledger costs 54-61 ms — so they were minted in **one**
batch and written by **one** `_append_lines` call, and the line above the hole survived.
A missing ``fsync`` loses a suffix, so it cannot have taken a line out of the middle, and
adding one would not have prevented that loss.

The process death is emulated with ``os._exit`` from inside the sequence being iterated:
that abandons the stream's buffer exactly as a kill does, and unlike a kill it happens at
a stated point, so the test is deterministic rather than a race.
"""

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
    """The one loaded ``events`` module, loaded by path the way a consumer would.

    Reused from ``sys.modules`` when a sibling test module has already loaded it: a second
    module object would mean a second ``LedgerError`` class, and an ``except`` in one file
    would stop matching a raise from the other.
    """
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

# The child abandons the stream mid-batch. It patches `_append_lines` with a wrapper that
# feeds it a sequence whose iteration ends the process, rather than sleeping or racing, so
# the residue on disk is whatever the stream had already handed the kernel at a stated
# point in the batch.
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

# One draft per line and no two alike: an identical payload is the same event id, which
# `append` skips as a replay, and a batch of replays writes one line however long it is.
# Fixed-width, so every line is the same length and the sizing below is arithmetic.
PAYLOAD = "%06d" + "e" * 240


@pytest.fixture(name="dying_append")
def fixture_dying_append(tmp_path: Path) -> Callable[[int], tuple[bytes, Path]]:
    """Run one append of *drafts* in a child that dies mid-batch; give back its residue."""
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
    """One child line's size on disk, serialized by the real writer rather than guessed.

    Within a few bytes: the sequence number and the carried count widen as the batch runs,
    and every caller here uses this to *size* a batch rather than to predict a byte count.
    """
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
    """`work-tracker.md` §4.4's choice, counted rather than restated.

    The positive control is the point — a spy that counted nothing because it was never
    installed would pass a bare ``== 0``.
    """
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
    # Opened for write because the control has to survive both platforms: `fsync` on a
    # read-only descriptor is legal on POSIX and is `EBADF` on Windows, where it maps to
    # `_commit`, so `O_RDONLY` made the control itself the failure (basicly-t31pvf).
    handle = os.open(str(tmp_path / events.INITIAL_LOG_NAME), os.O_RDWR)
    try:
        os.fsync(handle)
    finally:
        os.close(handle)
    assert calls == ["fsync"], "the spy counts nothing, so the zero above meant nothing"


def test_a_durable_write_asks_the_platform_for_no_sync_at_all() -> None:
    """The source-level half: a sync the exercised path skipped would still be a sync.

    ``O_SYNC``/``O_DSYNC`` are checked with ``fsync`` because they buy the same durability
    at the same cost to the lock hold, and a behavioural count would never see them.
    """
    with EVENTS_SOURCE.open("rb") as handle:
        # Tokenized rather than searched: the module's own prose says "No ``fsync``", and a
        # substring search over the source would find that sentence and pass on it.
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
    """A small batch cannot land in part: the stream never hands the kernel anything.

    ``BUFFER_CHUNK_BYTES`` is read as the floor, not as this platform's buffer, so a batch
    sized under it is atomic on every interpreter the kit supports rather than on this one.
    The module calls it "~8 KiB chunks", which was `io.DEFAULT_BUFFER_SIZE` up to 3.13 and
    is 131,072 from 3.14 — the floor reading is the one that survives that, and the assert
    below is what makes it a floor rather than a description.
    """
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
    """The bound `work-tracker.md` §4.4 leaves: a prefix, never a hole or a tear.

    Sized off ``io.DEFAULT_BUFFER_SIZE`` rather than off the floor, because the flush this
    depends on only happens once the batch exceeds the buffer this interpreter actually
    has.
    """
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
    """basicly-vkh0.30's signature, and the only instrument that ever saw it.

    A line taken out of the middle of a written log leaves nothing to parse and no gap the
    fold looks for, so the sequence hole itself is silent. The carried totals are not: the
    event minted above the hole counted the lost line, and a fold of what survives cannot
    reach that number. The next append restates the totals from the fold, which is
    `work-tracker.md` §4.6's "void until a fold restates them" — so exactly one event
    disagrees, which is why the live ledger showed one disagreement over 6,263 events
    rather than a run of them.
    """
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

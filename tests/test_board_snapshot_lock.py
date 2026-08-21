"""The supervisor lock fact crossing the producer boundary (basicly-rn0o.14).

Split from :mod:`test_board_snapshot`, which pins the producer's own sections against a
frozen corpus. This aspect is not the producer's alone: it is the one fact `board_snapshot`
may not derive, because reading it would close `supervise -> board_snapshot -> supervise`
(C11). `lint-imports` proves the direction is one-way; a copied fact needs a test to prove
the two ends still agree, which is the half layering cannot reach.

No frozen corpus fixture: every assertion here is on `session.holder`, which the caller
supplies whole.
"""

from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from basicly import board_snapshot, supervise

NOW = datetime(2026, 1, 2, tzinfo=UTC)

# The lock is stale by mtime while the decoy field claims it is fresh. `read_holder` takes the
# age from `st_mtime` ("staleness is mtime-only by design"), so an age read off the payload
# instead lands DECOY_AGE_S here and the two ends disagree about whether a supervisor is alive.
LOCK_AGE_S = 90.0
DECOY_AGE_S = 1.0
_PAYLOAD = {
    "pid": 41207,
    "session_id": "bc7cc925",
    "root_issue": "fx-root",
    "heartbeat_age_s": DECOY_AGE_S,
}


def _fixture_lock(repo_root: Path, payload: str) -> supervise.LockInfo:
    """What the one parser makes of a lock holding *payload*, `LOCK_AGE_S` old by mtime.

    Backdated rather than stubbing `supervise._now`, because mtime is the thing under test:
    a pinned clock passes whether the age came from the file or from the payload.
    """
    path = repo_root / supervise.LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime - LOCK_AGE_S, stat.st_mtime - LOCK_AGE_S))
    holder = supervise.read_holder(repo_root)
    assert holder is not None
    return holder


def _holder_of(repo_root: Path, holder: supervise.LockInfo) -> dict[str, Any]:
    """The `session.holder` triple a caller above `supervise` gets by passing *holder* down.

    `root_issue` is the caller's own knowledge of which pass runs rather than a lock field
    (unit B AC 3), and `stale` is `supervise`'s spelling of the horizon, since `LockInfo`
    carries the age and never the verdict.
    """
    facts = board_snapshot.SessionFacts(
        root_issue=holder.root_issue or "fx-root",
        supervised=True,
        session_id=holder.session_id or "",
        age_s=holder.age_s,
        stale=holder.age_s >= supervise.STALE_AFTER_S,
    )
    document = board_snapshot.build_document(
        repo_root, facts=board_snapshot.Facts(session=facts), now=NOW
    )
    session = cast("dict[str, Any]", document["session"])
    return cast("dict[str, Any]", session["holder"])


def test_the_snapshot_age_and_the_supervisors_own_reader_agree_on_one_lock(
    work_repo: Path,
) -> None:
    """One parser for the lock, and the producer never respells the age it is handed.

    A second age, taken from the payload rather than `st_mtime`, disagrees only once a
    supervisor has crashed - the one moment a wall display is worth reading. This is also
    the positive control for the corrupt-payload sibling: `id` has to be present somewhere,
    or its absence there is evidence about the probe rather than about the payload.
    """
    sound = _fixture_lock(work_repo, json.dumps(_PAYLOAD))
    emitted = _holder_of(work_repo, sound)

    assert emitted == {"id": "bc7cc925", "heartbeat_age_s": sound.age_s, "stale": True}
    assert emitted["heartbeat_age_s"] == pytest.approx(LOCK_AGE_S, abs=10.0)


def test_a_corrupt_payload_keeps_the_mtime_age_and_drops_the_identity(work_repo: Path) -> None:
    """`read_holder`'s recorded invariant, asserted through the producer rather than read.

    The reader promises "a corrupt payload still reports the heartbeat age ... with the
    identity fields None". For the board that means a crashed supervisor still shows an age -
    the wall says STALE rather than going blank - and shows no holder id it cannot stand behind.
    """
    corrupt = _fixture_lock(work_repo, "not json")
    assert (corrupt.pid, corrupt.session_id, corrupt.root_issue) == (None, None, None)

    absent = _holder_of(work_repo, corrupt)

    assert absent == {"heartbeat_age_s": corrupt.age_s, "stale": True}
    assert absent["heartbeat_age_s"] == pytest.approx(LOCK_AGE_S, abs=10.0)

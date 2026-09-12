from __future__ import annotations

import json
import os
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from basicly import board_snapshot, supervise

NOW = datetime(2026, 1, 2, tzinfo=UTC)

LOCK_AGE_S = 90.0
DECOY_AGE_S = 1.0
_PAYLOAD = {
    "pid": 41207,
    "session_id": "bc7cc925",
    "root_issue": "fx-root",
    "heartbeat_age_s": DECOY_AGE_S,
}


def _fixture_lock(repo_root: Path, payload: str) -> supervise.LockInfo:

    path = repo_root / supervise.LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(payload, encoding="utf-8")
    stat = path.stat()
    os.utime(path, (stat.st_atime - LOCK_AGE_S, stat.st_mtime - LOCK_AGE_S))
    holder = supervise.read_holder(repo_root)
    assert holder is not None
    return holder


def _holder_of(repo_root: Path, holder: supervise.LockInfo) -> dict[str, Any]:

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

    sound = _fixture_lock(work_repo, json.dumps(_PAYLOAD))
    emitted = _holder_of(work_repo, sound)

    assert emitted == {"id": "bc7cc925", "heartbeat_age_s": sound.age_s, "stale": True}
    assert emitted["heartbeat_age_s"] == pytest.approx(LOCK_AGE_S, abs=10.0)


def test_a_corrupt_payload_keeps_the_mtime_age_and_drops_the_identity(work_repo: Path) -> None:

    corrupt = _fixture_lock(work_repo, "not json")
    assert (corrupt.pid, corrupt.session_id, corrupt.root_issue) == (None, None, None)

    absent = _holder_of(work_repo, corrupt)

    assert absent == {"heartbeat_age_s": corrupt.age_s, "stale": True}
    assert absent["heartbeat_age_s"] == pytest.approx(LOCK_AGE_S, abs=10.0)

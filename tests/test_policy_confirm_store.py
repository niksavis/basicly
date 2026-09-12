from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import policy

if TYPE_CHECKING:
    import pytest


_LOCK_SAFETY_S = 10.0


def test_concurrent_confirm_code_writers_both_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    entered = threading.Event()
    release = threading.Event()
    real_read = policy._read_confirms
    first_call = True

    def racy_read(path: Path) -> dict[str, dict]:
        nonlocal first_call
        data = real_read(path)
        if first_call:
            first_call = False
            entered.set()
            assert release.wait(timeout=_LOCK_SAFETY_S), "the second writer never started"
        return data

    monkeypatch.setattr(policy, "_read_confirms", racy_read)
    codes = iter(["code-a", "code-b"])
    monkeypatch.setattr(policy, "_new_code", lambda: next(codes))
    monkeypatch.setattr(policy, "_now", lambda: 1000.0)

    def issue(issue_id: str) -> None:
        policy._issue_confirm_code(tmp_path, issue_id, "ship")

    first = threading.Thread(target=issue, args=("i1",))
    first.start()
    assert entered.wait(timeout=_LOCK_SAFETY_S), "the first writer never entered its read"
    second = threading.Thread(target=issue, args=("i2",))
    second.start()
    release.set()
    first.join(timeout=_LOCK_SAFETY_S)
    second.join(timeout=_LOCK_SAFETY_S)
    assert not first.is_alive(), "the first writer never returned"
    assert not second.is_alive(), "the second writer never returned"

    store = policy._read_confirms(tmp_path / policy._CONFIRM_FILE)
    assert policy._confirm_key("i1", "ship") in store, "the first writer's entry was lost"
    assert policy._confirm_key("i2", "ship") in store, "the second writer's entry was lost"


def test_a_reader_racing_a_write_never_sees_a_torn_confirm_store(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    path = tmp_path / policy._CONFIRM_FILE
    before = {"i1:ship": {"code": "code-a", "expires": 1000.0}}
    policy._write_confirms(path, before)
    mid_write: list[dict] = []
    real_replace = Path.replace

    def observing_replace(self: Path, target: str | Path) -> Path:
        mid_write.append(json.loads(Path(target).read_text(encoding="utf-8")))
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", observing_replace)
    after = before | {"i2:ship": {"code": "code-b", "expires": 1000.0}}
    policy._write_confirms(path, after)

    assert mid_write == [before], "the store was not swapped in whole by one replace"
    assert policy._read_confirms(path) == after

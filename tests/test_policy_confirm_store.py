"""The confirm-code store under concurrency: two writers, and a reader mid-write.

Split out of `tests/test_policy.py` under the `test_<module>_<aspect>` form that
`.scripts/check_test_naming.py` enforces, rather than banked as ratchet debt on a module
already 9x the read cap; these tests share none of that module's fixtures.
"""

from __future__ import annotations

import json
import threading
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import policy

if TYPE_CHECKING:
    import pytest


# A wedged lock must fail the test rather than hang the suite; never asserted as a
# duration.
_LOCK_SAFETY_S = 10.0


def test_concurrent_confirm_code_writers_both_land(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Two concurrent writers to the confirm-code store must not lose either write.

    An unguarded read-modify-write (basicly-kas8q7) lets the second writer's read
    predate the first writer's write, so its write clobbers the first entry. The
    stub holds the first writer inside its read until the second has started, so an
    unserialised store genuinely overlaps here.
    """
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
    """The store is swapped in whole, so a reader mid-write still parses the old map.

    Written in place it is observable half-written, and the reader that lands there is
    `_read_confirms` returning `{}` on the parse error — every unexpired code silently
    gone. The one window is the instant before the replace, so the read is taken there
    rather than by racing a thread and hoping to land in it.
    """
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

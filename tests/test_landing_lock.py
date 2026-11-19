from __future__ import annotations

import json
import os
import time
from pathlib import Path

import pytest

from basicly import cli, landing_lock, merge


def _age(repo: Path, seconds: float) -> None:
    path = repo / landing_lock.LOCK_FILE
    then = time.time() - seconds
    os.utime(path, (then, then))


def test_a_second_landing_is_refused_naming_the_holder_and_its_age(tmp_path: Path) -> None:
    with (
        landing_lock.hold(tmp_path, lane="lane-a", record="acme-1"),
        pytest.raises(landing_lock.LandingLockedError) as refused,
        landing_lock.hold(tmp_path, lane="lane-b", record="acme-2"),
    ):
        pytest.fail("a second landing took a held lock")
    text = str(refused.value)
    assert "lane-a" in text
    assert "acme-1" in text
    assert f"pid {os.getpid()}" in text
    assert "min old" in text
    assert not (tmp_path / landing_lock.LOCK_FILE).exists()


def test_a_waiting_landing_takes_the_lock_once_the_holder_releases_it(tmp_path: Path) -> None:
    path = tmp_path / landing_lock.LOCK_FILE
    with landing_lock.hold(tmp_path, lane="lane-a", record="acme-1"):
        pass
    released = {"done": False}

    def sleep(_seconds: float) -> None:
        path.unlink()
        released["done"] = True

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lane": "lane-a", "record": "acme-1"}), encoding="utf-8")
    with landing_lock.hold(tmp_path, lane="lane-b", record="acme-2", wait_s=60, sleep=sleep):
        assert json.loads(path.read_text(encoding="utf-8"))["lane"] == "lane-b"
    assert released["done"]


def test_the_lock_file_holds_its_owner_and_its_age_limit(tmp_path: Path) -> None:
    with landing_lock.hold(tmp_path, lane="lane-a", record="acme-1"):
        held = json.loads((tmp_path / landing_lock.LOCK_FILE).read_text(encoding="utf-8"))
    assert held == {
        "lane": "lane-a",
        "record": "acme-1",
        "pid": os.getpid(),
        "limit_s": landing_lock.LIMIT_S,
    }


def test_a_lock_over_its_limit_is_refused_by_name_and_never_taken(tmp_path: Path) -> None:
    path = tmp_path / landing_lock.LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lane": "lane-a", "record": "acme-1"}), encoding="utf-8")
    _age(tmp_path, landing_lock.LIMIT_S + 60)
    with (
        pytest.raises(landing_lock.LandingLockedError) as refused,
        landing_lock.hold(tmp_path, lane="lane-b", record="acme-2", wait_s=3600),
    ):
        pytest.fail("a stale lock was taken")
    assert "over its 60 min limit" in str(refused.value)
    assert "basicly worktree unlock --lane lane-a" in str(refused.value)
    assert json.loads(path.read_text(encoding="utf-8"))["lane"] == "lane-a"


def test_unlock_removes_only_the_named_lanes_lock(tmp_path: Path) -> None:
    path = tmp_path / landing_lock.LOCK_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"lane": "lane-a", "record": "acme-1"}), encoding="utf-8")
    with pytest.raises(landing_lock.LandingLockedError, match="held by lane-a, not lane-b"):
        landing_lock.unlock(tmp_path, "lane-b")
    assert path.exists()
    assert "removed the landing lock of lane-a" in landing_lock.unlock(tmp_path, "lane-a")
    assert not path.exists()
    assert landing_lock.unlock(tmp_path, "lane-a") == "no landing lock is held"


def test_basicly_worktree_merge_refuses_by_name_while_another_landing_holds_the_lock(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(cli, "_repo_root", lambda: tmp_path)
    monkeypatch.setattr(merge, "merge_worktree", lambda *_a, **_k: pytest.fail("landed"))
    monkeypatch.setattr(merge, "merge_queue", lambda *_a, **_k: pytest.fail("landed"))
    with landing_lock.hold(tmp_path, lane="lane-a", record="acme-1"):
        assert cli.main(["worktree", "merge", "lane-b", "--bead", "acme-2"]) == 1
        assert cli.main(["worktree", "merge-queue", "lane-b:acme-2"]) == 1
    assert capsys.readouterr().err.count("lane-a") == 2

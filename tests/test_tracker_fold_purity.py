from __future__ import annotations

import importlib.util
import json
import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import merge, owned_store, redact, tracker
from tests.kit_deployment_helpers import CLOCK, REPO_ROOT, events
from tests.test_merge_shard_compaction import LEDGER_DIR, WRITER, _git, repo

__all__ = ["repo"]

LEDGER = Path(LEDGER_DIR)
AUTHOR = "alicesmith"
HOOK_SOURCE = REPO_ROOT / owned_store.KIT_TRACKER_DIR / "install_hook.py"


def _write(root: Path, record: str, *, writer: str | None) -> None:
    events.append(
        root / LEDGER,
        [
            events.Draft(
                record,
                events.KIND_CREATED,
                {"title": f"record {record}", "created_by": AUTHOR, "note": f"filed by {AUTHOR}"},
            )
        ],
        actor="test",
        clock=lambda: CLOCK,
        writer=writer,
    )


def _trunk(root: Path) -> bytes:
    return b"".join(path.read_bytes() for path in sorted((root / LEDGER).glob("events-*.jsonl")))


def _ids(root: Path) -> set[str]:
    return {
        json.loads(line)["id"]
        for path in sorted((root / LEDGER).glob("*.jsonl"))
        for line in path.read_text(encoding="utf-8").splitlines()
        if line.strip()
    }


def _seeded(repo: Path) -> Path:
    _write(repo, "demo-trunk", writer="")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "seed trunk")
    _write(repo, "demo-shard", writer=WRITER)
    return repo


def test_two_usernames_fold_to_identical_trunk(
    repo: Path, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seeded(repo)
    other = tmp_path / "other"
    shutil.copytree(seeded, other)

    monkeypatch.setattr(redact.getpass, "getuser", lambda: AUTHOR)
    assert merge.commit_tracker_state(seeded, "basicly-x") is True
    monkeypatch.setattr(redact.getpass, "getuser", lambda: "bobjones")
    assert merge.commit_tracker_state(other, "basicly-x") is True

    assert tracker.pending_shards(seeded) == tracker.pending_shards(other) == ()
    assert _trunk(seeded) == _trunk(other)
    assert AUTHOR.encode() in _trunk(seeded)


def test_a_tracker_commit_never_rewrites_a_trunk_event(
    repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    seeded = _seeded(repo)
    before = _ids(seeded)
    monkeypatch.setattr(redact.getpass, "getuser", lambda: AUTHOR)

    assert merge.commit_tracker_state(seeded, "basicly-x") is True

    assert before <= _ids(seeded)


def _hook_body() -> str:
    spec = importlib.util.spec_from_file_location("fold_purity_install_hook", HOOK_SOURCE)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    script = (Path(owned_store.KIT_TRACKER_DIR) / "cli.py").as_posix()
    return module.body("python3", script, LEDGER.as_posix())


def _commits(root: Path) -> int:
    return int(_git(root, "rev-list", "--count", "HEAD").stdout.strip())


@pytest.mark.parametrize("ahead", [False, True], ids=["pull-only", "local-work-control"])
def test_a_pull_with_nothing_to_push_creates_no_commit(
    repo: Path, tmp_path: Path, ahead: bool
) -> None:
    _write(repo, "demo-shard", writer=WRITER)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a shard lands on main")
    origin = tmp_path / "origin.git"
    _git(tmp_path, "clone", "-q", "--bare", str(repo), str(origin))
    clone = tmp_path / "clone"
    _git(tmp_path, "clone", "-q", str(origin), str(clone))
    _git(clone, "config", "user.email", "t@t")
    _git(clone, "config", "user.name", "t")
    if ahead:
        (clone / "local.txt").write_text("local work\n", encoding="utf-8")
        _git(clone, "add", "local.txt")
        _git(clone, "commit", "-qm", "local work to push")
    before = _commits(clone)

    subprocess.run(["sh", "-c", _hook_body()], cwd=clone, check=True)

    assert _commits(clone) == before + (1 if ahead else 0)
    assert _git(clone, "status", "--porcelain").stdout.strip() == ""

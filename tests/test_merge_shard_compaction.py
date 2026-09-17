from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

from basicly import merge, owned_store, tracker, tracker_paths
from tests.kit_deployment_helpers import CLOCK, REPO_ROOT, events

LEDGER_DIR = tracker_paths.LEDGER_DIR_NAME
WRITER = "lane-one"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    (root / LEDGER_DIR).mkdir(parents=True)
    shutil.copytree(
        REPO_ROOT / owned_store.KIT_TRACKER_DIR,
        root / owned_store.KIT_TRACKER_DIR,
        ignore=shutil.ignore_patterns("__pycache__"),
    )
    for name in (".gitattributes", ".gitignore"):
        shutil.copy2(REPO_ROOT / name, root / name)
    _git(root.parent, "init", "-q", "-b", "main", str(root))
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


def _append(repo: Path, record: str, *, writer: str | None = None) -> None:
    events.append(
        repo / LEDGER_DIR,
        [events.Draft(record, events.KIND_CREATED, {"title": f"record {record}"})],
        actor="test",
        clock=lambda: CLOCK,
        writer=writer,
    )


def _records(repo: Path) -> set[str]:
    found, quarantined = events.read_events(repo / LEDGER_DIR)
    assert quarantined == []
    return set(events.fold(found).records)


def test_folding_moves_a_shard_into_the_trunk_and_reports_it(repo: Path) -> None:
    _append(repo, "demo-trunk", writer="")
    _append(repo, "demo-shard", writer=WRITER)
    assert tracker.pending_shards(repo) == (f"pending-{WRITER}.jsonl",)

    folded = tracker.fold_pending_shards(repo)

    assert folded == (f"pending-{WRITER}.jsonl",)
    assert tracker.pending_shards(repo) == ()
    assert _records(repo) == {"demo-trunk", "demo-shard"}


def test_the_tracker_commit_leaves_no_shard_behind(repo: Path) -> None:
    _append(repo, "demo-trunk", writer="")
    _append(repo, "demo-shard", writer=WRITER)
    assert tracker.pending_shards(repo), "the control: a shard is present before the commit"

    assert merge.commit_tracker_state(repo, "basicly-x") is True

    assert tracker.pending_shards(repo) == ()
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert "basicly-x" in _git(repo, "log", "-1", "--format=%s").stdout


def test_a_committed_shard_is_folded_even_though_the_tree_was_clean(repo: Path) -> None:
    _append(repo, "demo-shard", writer=WRITER)
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "a shard landed on its own")
    assert _git(repo, "status", "--porcelain").stdout.strip() == ""
    assert tracker.pending_shards(repo), "the control: the shard is tracked and the tree is clean"

    assert merge.commit_tracker_state(repo, "basicly-x") is True

    assert tracker.pending_shards(repo) == ()
    assert _records(repo) == {"demo-shard"}


def test_foreign_dirt_refuses_the_commit_and_leaves_the_shard_alone(repo: Path) -> None:
    _append(repo, "demo-shard", writer=WRITER)
    (repo / "app.py").write_text("print()\n", encoding="utf-8")

    assert merge.commit_tracker_state(repo, "basicly-x") is False

    assert tracker.pending_shards(repo) == (f"pending-{WRITER}.jsonl",)


def test_a_repository_with_no_kit_degrades_rather_than_raising(tmp_path: Path) -> None:
    bare = tmp_path / "bare"
    (bare / LEDGER_DIR).mkdir(parents=True)

    assert tracker.pending_shards(bare) == ()
    assert tracker.fold_pending_shards(bare) == ()

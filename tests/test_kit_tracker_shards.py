from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import (
    CLOCK,
    KIT_RELATIVE,
    REPO_ROOT,
    _load,
    events,
    fsck,
    snapshot,
)

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_shards_test_cli")

WRITER = "lane-one"
OTHER = "lane-two"


def _git(repo: Path, *args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(repo), *args], capture_output=True, text=True, check=True
    )


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "t@t")
    _git(root, "config", "user.name", "t")
    (root / "seed").write_text("seed\n", encoding="utf-8")
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", "seed")
    return root


def _append(ledger: Path, record: str, *, writer: str | None = None) -> None:
    events.append(
        ledger,
        [events.Draft(record, events.KIND_CREATED, {"title": f"record {record}"})],
        actor="test",
        clock=lambda: CLOCK,
        writer=writer,
    )


def _records(ledger: Path) -> set[str]:
    found, quarantined = events.read_events(ledger)
    assert quarantined == []
    return set(events.fold(found).records)


def test_a_directory_outside_a_repository_keeps_the_single_trunk_log(tmp_path: Path) -> None:
    _append(tmp_path, "demo-a")

    assert events.pending_paths(tmp_path) == []
    assert [path.name for path in events.log_paths(tmp_path)] == [events.INITIAL_LOG_NAME]


def test_an_existing_single_log_ledger_folds_unchanged_once_shards_appear(repo: Path) -> None:
    ledger = repo / "ledger"
    ledger.mkdir()
    _append(ledger, "demo-trunk", writer="")
    trunk_only = _records(ledger)

    _append(ledger, "demo-shard")

    assert trunk_only == {"demo-trunk"}
    assert _records(ledger) == {"demo-trunk", "demo-shard"}
    assert (ledger / events.INITIAL_LOG_NAME).exists()


def test_the_writer_is_the_branch_so_two_branches_append_to_two_files(repo: Path) -> None:
    ledger = repo / "ledger"
    ledger.mkdir()

    _git(repo, "checkout", "-qb", "feature/one")
    _append(ledger, "demo-one")
    _git(repo, "checkout", "-qb", "feature/two", "main")
    _append(ledger, "demo-two")

    assert [path.name for path in events.pending_paths(ledger)] == [
        "pending-feature-one.jsonl",
        "pending-feature-two.jsonl",
    ]


def test_a_detached_head_is_named_rather_than_falling_back_to_the_trunk(repo: Path) -> None:
    ledger = repo / "ledger"
    ledger.mkdir()
    head = _git(repo, "rev-parse", "HEAD").stdout.strip()
    _git(repo, "checkout", "-q", head)

    _append(ledger, "demo-detached")

    assert events.derive_writer(ledger) == events.DETACHED_WRITER
    assert (ledger / f"pending-{events.DETACHED_WRITER}.jsonl").exists()


def test_a_linked_worktree_writes_under_its_own_branch(repo: Path, tmp_path: Path) -> None:
    linked = tmp_path / "linked"
    _git(repo, "worktree", "add", "-q", "-b", "lane", str(linked))
    ledger = linked / "ledger"
    ledger.mkdir()

    _append(ledger, "demo-lane")

    assert [path.name for path in events.pending_paths(ledger)] == ["pending-lane.jsonl"]


def test_a_writer_naming_a_path_is_refused_rather_than_escaping_the_ledger(tmp_path: Path) -> None:
    with pytest.raises(events.LedgerError, match="must match"):
        events.pending_path(tmp_path, "../escape")


def test_compaction_moves_every_shard_into_the_trunk_and_unlinks_it(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    _append(tmp_path, "demo-one", writer=WRITER)
    _append(tmp_path, "demo-two", writer=OTHER)

    done = snapshot.compact(tmp_path)

    assert done.appended == 2
    assert done.duplicates == 0
    assert events.pending_paths(tmp_path) == []
    assert _records(tmp_path) == {"demo-trunk", "demo-one", "demo-two"}


def test_compaction_names_one_writer_and_leaves_the_others(tmp_path: Path) -> None:
    _append(tmp_path, "demo-one", writer=WRITER)
    _append(tmp_path, "demo-two", writer=OTHER)

    snapshot.compact(tmp_path, writers=(WRITER,))

    assert [path.name for path in events.pending_paths(tmp_path)] == [f"pending-{OTHER}.jsonl"]


def test_compacting_the_same_events_twice_appends_them_once(tmp_path: Path) -> None:
    _append(tmp_path, "demo-one", writer=WRITER)
    shard = events.pending_path(tmp_path, WRITER)
    carried = shard.read_text(encoding="utf-8")
    snapshot.compact(tmp_path)
    shard.write_text(carried, encoding="utf-8")

    second = snapshot.compact(tmp_path)

    assert second.appended == 0
    assert second.duplicates == 1
    assert _records(tmp_path) == {"demo-one"}
    trunk = events.append_target(tmp_path, writer="")
    assert len(trunk.read_text(encoding="utf-8").splitlines()) == 1


def test_compaction_is_a_no_op_on_a_ledger_holding_no_shard(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")

    done = snapshot.compact(tmp_path)

    assert done.shards == ()
    assert done.appended == 0


def test_rotation_refuses_while_a_shard_holds_uncompacted_events(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    _append(tmp_path, "demo-one", writer=WRITER)

    with pytest.raises(snapshot.SnapshotError, match="compact before rotating"):
        snapshot.rotate(tmp_path, "2027")


def test_rotation_proceeds_once_the_shard_is_compacted(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    _append(tmp_path, "demo-one", writer=WRITER)
    snapshot.compact(tmp_path)

    rotation = snapshot.rotate(tmp_path, "2027")

    assert rotation.log.name == "events-2027.jsonl"
    assert _records(tmp_path) == {"demo-trunk", "demo-one"}


def test_a_shard_makes_the_derived_snapshot_stale(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    snapshot.rebuild(tmp_path)
    assert not snapshot.staleness(tmp_path).stale

    _append(tmp_path, "demo-one", writer=WRITER)

    assert snapshot.staleness(tmp_path).stale


def test_fsck_is_reachable_from_the_one_command_a_consumer_knows(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _append(tmp_path, "demo-trunk", writer="")

    assert cli.main(["fsck", str(tmp_path)]) == cli.EXIT_OK

    report = json.loads(capsys.readouterr().out)
    assert report["clean"] is True
    assert report["records"] == 1


def test_fsck_rebuild_writes_the_derivatives_again(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    snapshot.rebuild(tmp_path)
    assert snapshot.snapshot_path(tmp_path).is_file(), "the control: a derivative exists"

    assert cli.main(["fsck", str(tmp_path), "--rebuild"]) == cli.EXIT_OK

    report = json.loads(capsys.readouterr().out)
    assert report["rebuilt"], "a rebuild names what it wrote"
    assert snapshot.snapshot_path(tmp_path).is_file()


def test_the_shard_gate_stays_quiet_at_the_warn_threshold(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    for index in range(fsck.SHARDS_WARN_ABOVE):
        events.pending_path(tmp_path, f"w{index:05d}").write_text("", encoding="utf-8")

    report = fsck.check(tmp_path)

    assert [found for found in report.findings if found.kind == fsck.SHARD_BACKLOG] == []


def test_the_shard_gate_warns_one_past_the_warn_threshold(tmp_path: Path) -> None:
    _append(tmp_path, "demo-trunk", writer="")
    for index in range(fsck.SHARDS_WARN_ABOVE + 1):
        events.pending_path(tmp_path, f"w{index:05d}").write_text("", encoding="utf-8")

    found = [one for one in fsck.check(tmp_path).findings if one.kind == fsck.SHARD_BACKLOG]

    assert len(found) == 1
    assert found[0].severity == fsck.WARNING


def test_the_shard_gate_refuses_one_past_the_refuse_threshold(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(fsck, "SHARDS_WARN_ABOVE", 2)
    monkeypatch.setattr(fsck, "SHARDS_REFUSE_ABOVE", 4)
    _append(tmp_path, "demo-trunk", writer="")
    for index in range(5):
        events.pending_path(tmp_path, f"w{index:05d}").write_text("", encoding="utf-8")

    report = fsck.check(tmp_path)
    found = [one for one in report.findings if one.kind == fsck.SHARD_BACKLOG]

    assert len(found) == 1
    assert found[0].severity == fsck.BROKEN
    assert not report.clean

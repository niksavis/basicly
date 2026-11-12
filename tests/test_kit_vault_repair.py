from __future__ import annotations

import importlib.util
import json
import sys
import time
from pathlib import Path
from types import ModuleType
from typing import Any

import pytest

from tests.test_kit_vault_shapes import WRITER_A, WRITER_B, Vault, _segment
from tests.test_kit_vault_share import _ids, _machine, _write, bare

REPO_ROOT = Path(__file__).resolve().parents[1]
KIT = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"
__all__ = ["bare"]


def _load(name: str, file_name: str) -> ModuleType:
    spec = importlib.util.spec_from_file_location(name, KIT / file_name)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


vault_repair = _load("basicly_tracker_kit_vault_repair", "vault_repair.py")
cli = _load("tracker_cli_vault_repair", "cli.py")
vault_clone = vault_repair.vault_clone
vault_queue = vault_repair.vault_queue
vault_share = vault_repair.vault_share
git_runner = vault_repair.git_runner


def _rev(repo: Path, name: str) -> str:
    return git_runner.run(["rev-parse", name], repo).strip()


def _queued(clone: Any) -> list[dict]:
    return vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)


def _rewritten(tmp_path: Path, bare: Path) -> dict[str, Any]:
    genesis = git_runner.run(["rev-list", "--max-parents=0", "main"], bare).strip()
    w_clone, w_state = _machine(tmp_path, bare, "w")
    origin = vault_queue.Origin("ws", False, time.time)
    claim = {"kind": "claim", "record": "acme-a1b2"}
    drafts = [claim, vault_queue.comment("acme-a1b2", "w note")]
    lost = vault_queue.write(w_clone, drafts, state=w_state, origin=origin)["events"]
    vault_share.share(w_clone, w_state)
    b_clone, b_state = _machine(tmp_path, bare, "b")
    vault_clone.fetch(b_clone)
    git_runner.run(["update-ref", "refs/heads/main", genesis], bare)
    x_clone, x_state = _machine(tmp_path, bare, "x")
    _write(x_clone, x_state, 1, "x")
    vault_share.share(x_clone, x_state)
    return {"lost": lost, "clone": b_clone, "state": b_state, "w_state": w_state}


def test_proof_t_seen_a_rewrite_is_refused_repaired_and_survives_a_reclone(
    tmp_path: Path, bare: Path
) -> None:
    case = _rewritten(tmp_path, bare)
    clone, state = case["clone"], case["state"]
    writer = vault_queue.writer_of(state)
    _write(clone, state, 1, "b")

    with pytest.raises(vault_share.ShareError, match="fsck --repair"):
        vault_share.share(clone, state)
    retracted = vault_clone.retracted(clone, git_runner.run, 30)
    repaired = vault_repair.repair(clone, state)
    requeue = [line for line in _queued(clone) if line["kind"] == "requeue"]
    shared = vault_share.share(clone, state)
    recloned = vault_repair.reclone(clone)

    assert list(retracted) == ["refs/basicly/retracted/1"]
    assert repaired["requeue_event"] == 2
    assert repaired["claim_lost"] == 1
    assert len(requeue) == 1
    assert sorted(line["id"] for line in requeue[0]["payload"]["events"]) == sorted(case["lost"])
    assert shared["pending"] == 0
    assert "refs/basicly/seen" in recloned["refs"]
    assert "refs/basicly/retracted/1" in recloned["refs"]
    assert vault_queue.writer_of(state) == writer


def test_a_retracted_tip_is_stored_once_however_often_fetch_runs(
    tmp_path: Path, bare: Path
) -> None:
    case = _rewritten(tmp_path, bare)

    vault_clone.fetch(case["clone"])
    vault_clone.fetch(case["clone"])

    assert len(vault_clone.retracted(case["clone"], git_runner.run, 30)) == 1


def test_own_lost_lines_are_requeued_as_they_were(tmp_path: Path, bare: Path) -> None:
    genesis = _rev(bare, "main")
    clone, state = _machine(tmp_path, bare, "b")
    mine = _write(clone, state, 2, "mine")
    vault_share.share(clone, state)
    git_runner.run(["update-ref", "refs/heads/main", genesis], bare)
    other, other_state = _machine(tmp_path, bare, "x")
    _write(other, other_state, 1, "x")
    vault_share.share(other, other_state)
    vault_clone.fetch(clone)

    shown = vault_repair.repair(clone, state)

    assert shown["requeued"] == 2
    assert sorted(line["id"] for line in _queued(clone)) == sorted(mine)
    assert vault_share.share(clone, state)["pending"] == 0
    assert all(_ids(bare)[i] == 1 for i in mine)


def _quarantined(tmp_path: Path, bare: Path) -> str:
    work = Vault(tmp_path / "work")
    work.git("pull", "-q", str(bare), "main")
    forged: dict[str, object] = {"kind": "comment", "writer": WRITER_B, "id": "q1", "record": "r"}
    work.write(f"log/{WRITER_A}/00001.jsonl", _segment(WRITER_A, forged))
    commit = work.commit("forged")
    work.git("push", "-q", str(bare), "HEAD:main")
    return commit


def test_proof_t_quarantine_kept_fsck_names_it_and_repair_needs_no_fetch(
    tmp_path: Path, bare: Path
) -> None:
    bad = _quarantined(tmp_path, bare)
    clone, state = _machine(tmp_path, bare, "b")
    vault_clone.fetch(clone)
    calls: list[str] = []

    def offline(args: list[str], cwd: Path, **options: Any) -> str:
        if {"fetch", "push", "ls-remote"} & set(args):
            calls.append(args[0])
            raise git_runner.GitCommandError("fetch", 128, "offline")
        return git_runner.run(args, cwd, **options)

    before = vault_repair.report(clone)
    repaired = vault_repair.repair(clone, state, runner=offline)
    after = vault_repair.report(clone)
    requeue = [line for line in _queued(clone) if line["kind"] == "requeue"]

    assert [(q["commit"], q["processed"]) for q in before["quarantined"]] == [(bad, False)]
    assert "writer other than its segment writer" in before["quarantined"][0]["rule"]
    assert repaired["processed"] == [bad]
    assert calls == []
    assert [line["id"] for line in requeue[0]["payload"]["events"]] == ["q1"]
    assert after["quarantined"][0]["processed"] is True


def test_accept_rewrite_needs_purge_and_the_fetched_head_then_rebuilds_the_ids(
    tmp_path: Path, bare: Path
) -> None:
    case = _rewritten(tmp_path, bare)
    clone = case["clone"]
    vault_clone.fetch(clone)
    head = _rev(clone.bare, vault_clone.HEAD_REF)

    with pytest.raises(vault_repair.RepairError, match="--purge"):
        vault_repair.accept_rewrite(clone, head, purge=False)
    with pytest.raises(vault_repair.RepairError, match="fetched head"):
        vault_repair.accept_rewrite(clone, "0" * 40, purge=True)
    shown = vault_repair.accept_rewrite(clone, head, purge=True)

    assert shown["purged"] == ["refs/basicly/retracted/1"]
    assert vault_clone.retracted(clone, git_runner.run, 30) == {}
    assert vault_clone.valid_ids(clone) == set(_ids(bare))
    assert (clone.folder / vault_clone.COVERS_NAME).read_text().strip() == head
    assert vault_share.share(clone, case["state"])["shared"] == 0


def test_fsck_repair_through_the_cli_prints_the_counts(
    tmp_path: Path, bare: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    case = _rewritten(tmp_path, bare)
    vault_clone.fetch(case["clone"])
    vault = str(bare)
    state = str(case["state"])

    code = cli.main(["fsck", "--vault", vault, "--state", state, "--repair"])
    shown = json.loads(capsys.readouterr().out)

    assert code == cli.EXIT_OK
    assert shown["requeue_event"] == 2
    assert shown["claim_lost"] == 1


def test_no_basicly_ref_ever_reaches_the_remote_after_share_repair_and_reclone(
    tmp_path: Path, bare: Path
) -> None:
    case = _rewritten(tmp_path, bare)
    clone, state = case["clone"], case["state"]
    vault_clone.fetch(clone)

    vault_repair.repair(clone, state)
    vault_share.share(clone, state)
    vault_repair.reclone(clone)
    vault_share.share(clone, state)
    local = git_runner.run(["for-each-ref", "--format=%(refname)", "refs/basicly/"], clone.bare)
    remote = git_runner.run(["for-each-ref", "--format=%(refname)"], bare).split()

    assert "refs/basicly/retracted/1" in local.split()
    assert remote == ["refs/heads/main"]

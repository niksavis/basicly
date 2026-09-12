from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any

import pytest

from basicly import policy, run_record, tracker
from basicly.config import PolicyConfig

REPO_ROOT = Path(__file__).resolve().parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"

ENGINE_PROVIDER = "basicly-verify"
FOREIGN_PROVIDER = "some-ci"


def _repo(tmp_path: Path, mode: str = tracker.MODE_OWNED) -> Path:
    (tmp_path / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, tmp_path / tracker.KIT_TRACKER_DIR / source.name)
    (tmp_path / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (tmp_path / "basicly.toml").write_text(f'[tracker]\nmode = "{mode}"\n', encoding="utf-8")
    return tmp_path


@pytest.fixture(autouse=True)
def no_spawn(monkeypatch: pytest.MonkeyPatch) -> None:

    def refuse(cmd: list[str], **_kwargs: object) -> None:
        pytest.fail(f"the engine spawned a process after the flip: {cmd}")

    monkeypatch.setattr(subprocess, "run", refuse)


def _ledger_events(repo: Path) -> list[Any]:
    kit = tracker.kit(repo)
    return kit.read_ledger(tracker.ledger_dir(repo))


def _kinds(repo: Path, record: str) -> list[str]:
    return [event.kind for event in _ledger_events(repo) if event.record == record]


def test_a_bead_no_store_holds_reads_as_none(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    tracker.create_record(repo, ["create", "a bead", "-t", "task", "--parent", "seam-0", "--json"])

    assert tracker.read_record(repo, "seam-9999") is None


def test_require_record_raises_one_message_naming_the_bead(tmp_path: Path) -> None:
    repo = _repo(tmp_path)
    with pytest.raises(RuntimeError, match="the tracker holds no usable record for seam-9999"):
        tracker.require_record(repo, "seam-9999")


def test_an_empty_ledger_reads_as_absence_not_as_a_failure(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    assert tracker.read_record(repo, "seam-0001") is None


def test_a_tombstoned_record_reads_as_absent_after_the_flip(tmp_path: Path) -> None:

    repo = _repo(tmp_path)
    record = tracker.create_record(
        repo, ["create", "a bead", "-t", "task", "--parent", "s-1", "--json"]
    )
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo), [kit.events.Draft(record, kit.events.KIND_TOMBSTONE, {})]
    )

    assert tracker.read_record(repo, record) is None
    assert record not in tracker.all_views(repo)


def test_no_module_outside_the_seam_reads_the_owned_store() -> None:

    root = REPO_ROOT / "src" / "basicly"
    reaching = {
        "tracker.owned_record(",
        "tracker.tracker_mode(",
        "tracker.ledger_dir(",
        "tracker.kit(",
    }
    offenders = sorted(
        f"{path.name}: {name}"
        for path in sorted(root.glob("*.py"))
        if path.name != "tracker.py"
        for name in reaching
        if name in path.read_text(encoding="utf-8")
    )
    assert offenders == []


def _run(seed: str, *, tokens: int) -> run_record.RunRecord:
    return run_record.RunRecord(
        agent="claude",
        outcome="EXECUTED",
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", "<prompt>"),
        timestamp="2026-08-07T00:00:00+00:00",
        tokens=tokens,
        prompt_sha256=seed * 64,
        phase="build",
    )


def _owned_repo(tmp_path: Path, *records: str) -> Path:

    repo = _repo(tmp_path, tracker.MODE_OWNED)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return repo


def test_the_engine_carries_every_marker_family_with_br_absent(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "seam-0001", "seam-0002")
    config = PolicyConfig(required_gates=("verify",), max_rework=2, autonomy="L3")

    policy.approve_checkpoint(repo, "seam-0001", "ship")
    assert policy.spend_gate_override(repo, "seam-0001", "verify") is True
    policy.record_unreliable_gate(repo, "seam-0001", "verify", "passed unchanged")
    grant = policy.issue_grant_guarded(repo, "seam-0001", "L3", 8_000_000, config, interactive=True)
    charged = policy.record_rework(repo, "seam-0001", "verify")
    ident = run_record.record_marker(repo, "seam-0001", _run("a", tokens=1234))
    other = run_record.record_marker(repo, "seam-0002", _run("c", tokens=99))

    assert policy.checkpoint_approved(repo, "seam-0001", "ship") is True
    assert policy.gate_override_spent(repo, "seam-0001", "verify") is True
    assert policy.unreliable_gate_events(repo, "seam-0001", "verify") == 1
    assert grant.status == "approved"
    active = policy.active_grant(repo, "seam-0001")
    assert active is not None
    assert (active.level, active.token_budget) == ("L3", 8_000_000)
    assert charged == 1
    assert policy.rework_charged(repo, "seam-0001", "verify") == 1
    assert ident is not None and other is not None
    history = run_record.tracker_history(repo)
    assert [entry["tokens"] for entry in history["seam-0001"]] == [1234]
    assert [entry["tokens"] for entry in history["seam-0002"]] == [99]
    assert policy.checkpoint_approved(repo, "seam-0002", "ship") is False


def test_a_second_dispatch_record_is_told_from_the_first_without_br(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "seam-0001")
    record = _run("b", tokens=10)

    first = run_record.record_marker(repo, "seam-0001", record)
    second = run_record.record_marker(repo, "seam-0001", record)

    assert first != second
    assert len(run_record.tracker_history(repo)["seam-0001"]) == 2


def test_the_marker_stamp_survives_the_flip_so_a_wait_stays_measurable(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "seam-0001")

    wait_id = policy.record_wait_request(repo, "seam-0001", "ship")
    assert wait_id is not None
    event = policy.record_checkpoint_wait(repo, "seam-0001", "ship", by="human", delegated=False)

    assert event is not None
    assert event.wait_id == wait_id
    assert event.requested_at
    assert policy.wait_events(repo, "seam-0001")[0].wait_id == wait_id


def test_a_marker_write_is_still_refused_inside_a_read_only_section(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "seam-0001")

    with (
        tracker.read_only("a pre-flight gate"),
        pytest.raises(tracker.TrackerWriteRefusedError) as excinfo,
    ):
        tracker.add_comment(repo, "seam-0001", "[harness-policy] recorded from a gate")

    assert "a pre-flight gate" in str(excinfo.value)
    assert tracker.read_comments(repo, "seam-0001") == []


def test_the_soft_marker_write_is_refused_too(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "seam-0001")

    with tracker.read_only("a pre-flight gate"), pytest.raises(tracker.TrackerWriteRefusedError):
        tracker.try_add_comment(repo, "seam-0001", "[harness-run] id=x phase=build")


def test_a_tombstoned_records_markers_read_as_absent(tmp_path: Path) -> None:

    repo = _owned_repo(tmp_path, "seam-0001")
    tracker.add_comment(repo, "seam-0001", "[harness-policy] rework gate=verify")
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo), [kit.events.Draft("seam-0001", kit.events.KIND_TOMBSTONE, {})]
    )

    assert tracker.read_comments(repo, "seam-0001") == []
    assert tracker.all_comment_texts(repo) == {}


def test_a_counter_refuses_to_read_a_store_that_cannot_answer(tmp_path: Path) -> None:

    repo = _repo(tmp_path, tracker.MODE_OWNED)
    for source in (repo / tracker.KIT_TRACKER_DIR).glob("*.py"):
        source.unlink()

    with pytest.raises(RuntimeError):
        tracker.read_comments(repo, "seam-0001")
    assert tracker.try_read_comments(repo, "seam-0001") == []
    assert tracker.all_comment_texts(repo) == {}

from __future__ import annotations

import time
from pathlib import Path

import pytest

from basicly import decisions, policy, tracker, tracker_paths
from basicly.config import VERIFY_GATE_PROVIDER
from tests import flipped_tracker

ANSWERER = "an-operator"

_WAITED_S = 600

_SLACK_S = 5

_STAMP_RESOLUTION_S = 1


def _assert_interval(waited_s: int, elapsed_s: float) -> None:

    assert _WAITED_S - _STAMP_RESOLUTION_S <= waited_s <= _WAITED_S + elapsed_s + _SLACK_S


@pytest.fixture
def probe_repo(tmp_path: Path) -> Path:
    repo = flipped_tracker.flipped_repo(tmp_path / "tracker")
    (repo / "basicly.toml").write_text(
        '[tracker]\nmode = "owned"\nprefix = "probe"\n', encoding="utf-8"
    )
    return repo


@pytest.fixture
def issue_id(probe_repo: Path) -> str:
    return tracker.create_record(probe_repo, ["create", "probe wait", "-t", "task", "--json"])


@pytest.fixture
def answered_late(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(policy, "_now", lambda: time.time() + _WAITED_S)


def test_the_tracker_stamps_and_reports_a_parseable_created_at(
    probe_repo: Path, issue_id: str
) -> None:
    tracker.add_comment(probe_repo, issue_id, "probe")

    (comment,) = tracker.read_comments(probe_repo, issue_id)
    stamp = policy._parse_ts(str(comment.get("created_at", "")))

    assert stamp is not None, f"the ledger recorded no parseable stamp: {comment!r}"
    assert abs(stamp.timestamp() - time.time()) < 60


@pytest.mark.usefixtures("answered_late")
def test_a_legacy_checkpoint_wait_resumes_under_task_authorization_in_the_real_tracker(
    probe_repo: Path, issue_id: str
) -> None:
    started = time.monotonic()
    tracker.add_comment(
        probe_repo,
        issue_id,
        f"[harness-wait] id={issue_id}#wait-ship kind=checkpoint requested",
    )
    kit = tracker.kit(probe_repo)
    gates = tracker.kit(probe_repo, "gates")
    kit.events.append(
        tracker.ledger_dir(probe_repo),
        [gates.gate_draft(issue_id, "verify", provider=VERIFY_GATE_PROVIDER, passed=True)],
    )

    approved = policy.approve_checkpoint_guarded(probe_repo, issue_id, "ship", interactive=False)
    assert approved.status == "approved", approved.detail
    assert approved.code is None
    assert policy.active_grant(probe_repo, issue_id) is None
    assert all(
        "grant level=" not in row["text"] for row in tracker.read_comments(probe_repo, issue_id)
    )
    elapsed = time.monotonic() - started

    (event,) = policy.wait_events(probe_repo, issue_id)
    assert (event.kind, event.subject) == ("checkpoint", "ship")
    assert (event.answered_by, event.delegated) == ("task-request", False)
    _assert_interval(event.waited_s, elapsed)
    resumed = policy.approve_checkpoint_guarded(probe_repo, issue_id, "ship", interactive=False)
    assert resumed.status == "approved"
    assert policy.wait_events(probe_repo, issue_id) == (event,)


@pytest.mark.usefixtures("answered_late")
def test_a_queued_decision_wait_round_trips_through_the_real_tracker(
    probe_repo: Path, issue_id: str
) -> None:
    started = time.monotonic()
    item = decisions.enqueue(probe_repo, issue_id, "needs-input", "which db?")

    decisions.answer(probe_repo, item.decision_id, "postgres", by=ANSWERER)
    elapsed = time.monotonic() - started

    (event,) = policy.wait_events(probe_repo, issue_id)
    assert (event.wait_id, event.kind, event.subject) == (
        item.decision_id,
        "decision",
        "needs-input",
    )
    assert (event.answered_by, event.delegated) == (ANSWERER, False)
    _assert_interval(event.waited_s, elapsed)


@pytest.mark.usefixtures("answered_late")
def test_the_wait_marker_travels_in_the_committed_ledger(probe_repo: Path, issue_id: str) -> None:

    item = decisions.enqueue(probe_repo, issue_id, "needs-input", "which db?")
    decisions.answer(probe_repo, item.decision_id, "postgres", by=ANSWERER)

    logs = sorted((probe_repo / tracker_paths.LEDGER_DIR_NAME).glob("events-*.jsonl"))
    committed = "".join(path.read_text(encoding="utf-8") for path in logs)

    assert f"{policy.WAIT_MARKER} id={item.decision_id} kind=decision answered" in committed

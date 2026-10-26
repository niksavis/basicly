from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any

import pytest

from basicly import board_activity, board_serve, owned_store


@pytest.fixture
def activity_repo(work_repo: Path) -> Path:
    kit = owned_store.kit(work_repo)
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    report = {
        "action": "Editing the activity view",
        "phase": "implementation",
        "runtime": "running",
    }
    kit.events.append(
        ledger,
        [
            kit.events.Draft("acme-root", kit.events.KIND_CREATED, {"title": "Purposeful work"}),
            kit.events.Draft("acme-root", kit.events.KIND_STATUS, {"status": "in_progress"}),
            kit.events.Draft(
                "acme-root.1",
                kit.events.KIND_CREATED,
                {
                    "title": "Readable activity",
                    "description": "## Context\nSee what is happening.\n",
                },
            ),
            kit.events.Draft("acme-root.1", kit.events.KIND_STATUS, {"status": "in_progress"}),
            kit.events.Draft(
                "acme-root.1",
                kit.migrate.KIND_EDGE,
                {
                    kit.migrate.EDGE_FROM: "acme-root.1",
                    kit.migrate.EDGE_TO: "acme-root",
                    kit.migrate.EDGE_TYPE: "parent-child",
                },
            ),
            kit.events.Draft(
                "acme-root.1",
                kit.events.KIND_COMMENT,
                {
                    "text": board_activity.PREFIX + json.dumps(report),
                },
            ),
        ],
        actor="agent:fixture",
    )
    return work_repo


def _append(repo: Path, text: str, record: str = "acme-root.1") -> None:
    kit = owned_store.kit(repo)
    kit.events.append(
        owned_store.ledger_dir(repo),
        [
            kit.events.Draft(record, kit.events.KIND_COMMENT, {"text": text}),
        ],
        actor="agent:fixture",
    )


def test_activity_reports_task_state_without_inventing_runtime(activity_repo: Path) -> None:
    body, _ = board_activity.Feed(activity_repo).payload("acme-root")
    report = json.loads(body)
    assert report["total"] == 2
    row = next(item for item in report["items"] if item["id"] == "acme-root.1")
    assert row["status"] == "in_progress"
    assert row["runtime"] == "unknown"
    assert row["purpose"] == "See what is happening."
    assert row["phase"] == "implementation"
    assert row["action"] == "Editing the activity view"
    assert row["started_at"]
    assert row["logs"][0]["writer"] == "agent:fixture"


def test_unchanged_reads_reuse_payload_and_real_writes_invalidate_it(
    activity_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    feed = board_activity.Feed(activity_repo)
    before, tag = feed.payload("acme-root")
    kit = owned_store.kit(activity_repo)
    original = kit.read_ledger
    reads = []

    def observed(path: Path) -> Any:
        reads.append(path)
        return original(path)

    monkeypatch.setattr(kit, "read_ledger", observed)
    assert feed.payload("acme-root") == (before, tag)
    assert not reads
    _append(activity_repo, board_activity.PREFIX + json.dumps({"action": "Checking the view"}))
    after, changed_tag = feed.payload("acme-root")
    assert reads
    assert after != before and changed_tag != tag
    assert "Checking the view" in after.decode()


def test_recent_history_is_bounded_and_unknown_roots_are_refused(activity_repo: Path) -> None:
    for number in range(8):
        _append(activity_repo, f"Observed action {number}")
    feed = board_activity.Feed(activity_repo)
    body, _ = feed.payload("acme-root")
    row = next(item for item in json.loads(body)["items"] if item["id"] == "acme-root.1")
    assert len(row["logs"]) == 5
    assert row["logs"][-1]["text"] == "Observed action 7"
    assert row["action"] == "Observed action 7"
    assert row["action_at"] == row["logs"][-1]["at"]
    with pytest.raises(KeyError):
        feed.payload("acme-missing")


@pytest.mark.parametrize("query", ["root=a&root=b", "extra=secret", "root=" + "a" * 121])
def test_activity_query_refuses_ambiguous_or_unbounded_inputs(query: str) -> None:
    assert board_activity.root_query("root=acme-root") == "acme-root"
    with pytest.raises(ValueError):
        board_activity.root_query(query)


def test_served_activity_updates_without_document_reload(activity_repo: Path) -> None:
    listener = board_serve.bind(activity_repo, port=0, actions=False)
    thread = threading.Thread(target=listener.run, daemon=True)
    thread.start()
    try:
        with urllib.request.urlopen(listener.url + "/activity", timeout=20) as response:
            page = response.read().decode()
            assert "Work activity" in page
            assert "Refresh" not in response.headers
            assert "fetch(" in page
        url = listener.url + "/activity.json?root=acme-root"
        with urllib.request.urlopen(url, timeout=20) as response:
            tag = response.headers["ETag"]
            assert json.load(response)["total"] == 2
        request = urllib.request.Request(url, headers={"If-None-Match": tag})
        with pytest.raises(urllib.error.HTTPError) as unchanged:
            urllib.request.urlopen(request, timeout=20)
        assert unchanged.value.code == 304
        _append(activity_repo, "A consumer observed the completed check.")
        with urllib.request.urlopen(request, timeout=20) as response:
            assert response.status == 200
            assert response.headers["ETag"] != tag
            assert "completed check" in response.read().decode()
    finally:
        listener.stop()
        listener.close()
        thread.join(timeout=20)


def test_handover_is_preserved_without_replacing_the_meaningful_action(activity_repo: Path) -> None:
    _append(activity_repo, "[model handoff] Resume the remaining acceptance checks.")
    _append(activity_repo, "[session handover 2026-10-26] Administrative note.", "acme-root")
    body, _ = board_activity.Feed(activity_repo).payload("acme-root")
    row = next(item for item in json.loads(body)["items"] if item["id"] == "acme-root.1")
    assert json.loads(body)["items"][0]["id"] == "acme-root.1"
    assert row["action"] == "Editing the activity view"
    assert row["logs"][-1]["text"].startswith("[model handoff]")

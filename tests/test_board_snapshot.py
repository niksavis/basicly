from __future__ import annotations

import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from basicly import (
    board_schema,
    board_sections,
    board_snapshot,
    owned_store,
    run_record,
)

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger" / "events-0001.jsonl"
MINIMAL = REPO_ROOT / "tests" / "fixtures" / "board" / "minimal-v1.json"

BUILD_CAP_S = 1.5

NOW = datetime(2026, 1, 2, tzinfo=UTC)

FIXTURE_TOTAL = 6
FIXTURE_CLOSED = 2
FIXTURE_IN_PROGRESS = 1

FIXTURE_EDGES = 7


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def _built(repo_root: Path, **kwargs: Any) -> dict[str, Any]:

    return cast("dict[str, Any]", board_snapshot.build_document(repo_root, **kwargs))


def _run_records(repo_root: Path, records: dict) -> None:
    path = repo_root / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _dispatch(**overrides: object) -> dict:
    entry = {
        "agent": "claude",
        "outcome": "executed",
        "returncode": 0,
        "duration_s": 1.0,
        "command": ["claude"],
        "timestamp": "2026-01-01T00:00:00+00:00",
        "cost": 2.0,
        "input_tokens": 10,
        "output_tokens": 20,
        "cache_read_tokens": 30,
        "cache_write_tokens": 40,
    }
    entry.update(overrides)
    return entry


def test_the_document_conforms_and_a_stripped_one_does_not(board_repo: Path) -> None:

    document = _built(board_repo, now=NOW)
    verdict = board_schema.verdict(board_repo, document)
    assert verdict.outcome == board_schema.OK, verdict.summary
    assert verdict.exit_code == 0
    assert not verdict.unknown, verdict.unknown

    stripped = {key: value for key, value in document.items() if key != "freshness"}
    assert board_schema.verdict(board_repo, stripped).exit_code == 1
    assert board_schema.validate_file(board_repo, MINIMAL).exit_code == 0


def test_the_ledger_is_folded_once_and_no_subprocess_is_spawned(
    board_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    kit = owned_store.kit(board_repo)
    folds = []
    real_fold = kit.events.fold

    def counting_fold(*args: object, **kwargs: object) -> object:
        folds.append(1)
        return real_fold(*args, **kwargs)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the producer spawned a subprocess")

    monkeypatch.setattr(kit.events, "fold", counting_fold)
    monkeypatch.setattr(subprocess, "Popen", refuse)
    monkeypatch.setattr("os.system", refuse)
    monkeypatch.setattr("os.posix_spawn", refuse, raising=False)

    document = _built(board_repo, now=NOW)

    assert len(folds) == 1
    assert document["backlog"]["total"] == FIXTURE_TOTAL
    assert document["units"]
    assert document["graph"]["edges"]


def test_a_build_on_this_repos_corpus_stays_under_the_cap() -> None:
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        board_snapshot.build_document(REPO_ROOT)
        samples.append(time.perf_counter() - started)
    assert sorted(samples)[2] < BUILD_CAP_S, samples


def test_the_session_section_is_omitted_until_the_caller_supplies_the_lock_facts(
    board_repo: Path,
) -> None:
    assert "session" not in _built(board_repo, now=NOW)

    facts = board_snapshot.SessionFacts(
        root_issue="fx-root", supervised=True, session_id="bc7cc925", age_s=6.0, stale=False
    )
    section = _built(board_repo, facts=board_snapshot.Facts(session=facts), now=NOW)["session"]
    assert section == {
        "root": "fx-root",
        "supervised": True,
        "root_status": "open",
        "holder": {"id": "bc7cc925", "heartbeat_age_s": 6.0, "stale": False},
    }


def test_the_lanes_section_is_omitted_until_the_caller_supplies_the_lane_facts(
    board_repo: Path,
) -> None:

    assert "lanes" not in _built(board_repo, now=NOW)

    empty = _built(board_repo, facts=board_snapshot.Facts(lanes=[]), now=NOW)
    assert empty["lanes"] == []
    assert board_schema.verdict(board_repo, empty).exit_code == 0

    supplied = board_sections.LaneFacts(id="fx-root.1", phase="verify")
    document = _built(board_repo, facts=board_snapshot.Facts(lanes=[supplied]), now=NOW)
    assert document["lanes"] == [{"id": "fx-root.1", "phase": "verify"}]
    assert board_schema.verdict(board_repo, document).exit_code == 0


def test_a_holder_the_caller_could_not_read_leaves_the_triple_out(board_repo: Path) -> None:
    facts = board_snapshot.SessionFacts(root_issue="fx-root")
    section = _built(board_repo, facts=board_snapshot.Facts(session=facts), now=NOW)["session"]
    assert "holder" not in section
    assert section["supervised"] is False


def test_the_backlog_and_the_ask_pin_the_frozen_corpus(board_repo: Path) -> None:
    document = _built(board_repo, now=NOW)
    assert document["backlog"] == {
        "total": FIXTURE_TOTAL,
        "active": FIXTURE_TOTAL - FIXTURE_CLOSED,
        "in_progress": FIXTURE_IN_PROGRESS,
        "closed": FIXTURE_CLOSED,
        "closed_today": 0,
        "by_priority": {"P0": 1, "P1": 2, "P2": 2, "P3": 1},
    }
    assert not {"ready", "blocked"} & set(document["backlog"])
    assert [ask["wait_id"] for ask in document["asks"]] == ["fx-root.1#wait-ship"]
    assert len(document["events"]) == board_snapshot.EVENT_LIMIT


def test_the_units_and_graph_sections_pin_the_frozen_corpus(board_repo: Path) -> None:

    document = _built(board_repo, now=NOW)

    assert [row["id"] for row in document["units"]] == [
        "fx-root",
        "fx-root.1",
        "fx-root.3",
        "fx-root.4",
    ]
    assert len(document["units"]) == FIXTURE_TOTAL - FIXTURE_CLOSED
    assert document["units"][1] == {
        "id": "fx-root.1",
        "title": "a lane in flight",
        "status": "in_progress",
        "priority": "P1",
        "type": "task",
        "owes": ["## Trigger", "## Acceptance Criteria"],
    }
    drawn = {row["id"] for row in document["units"]}
    edges = document["graph"]["edges"]
    assert len(edges) == FIXTURE_EDGES
    assert all(edge["from"] in drawn or edge["to"] in drawn for edge in edges)
    assert {"from": "fx-root.1", "to": "fx-root.5", "kind": "blocks"} not in edges
    assert not any({"ready", "phase"} & set(row) for row in document["units"])
    assert board_schema.verdict(board_repo, document).exit_code == 0


def test_the_callers_derivations_reach_every_section_that_needs_one(board_repo: Path) -> None:

    facts = board_snapshot.Facts(
        session=board_snapshot.SessionFacts(
            root_issue="fx-root", grant_level="L3", token_budget=80000000, spent_tokens=12
        ),
        repo=board_sections.RepoFacts(branch="harness/fx", head="7c930755", dirty=True),
        phases={"fx-root.1": "verify"},
        readiness=board_sections.Readiness(
            ready=frozenset({"fx-root.1", "fx-root.3"}), blocked=frozenset({"fx-root.4"})
        ),
        questions={"fx-root.1#wait-ship": "ship it?"},
    )
    document = _built(board_repo, facts=facts, now=NOW)
    rows = {row["id"]: row for row in document["units"]}

    assert document["repo"]["branch"] == "harness/fx"
    assert document["repo"]["dirty"] is True
    assert (document["backlog"]["ready"], document["backlog"]["blocked"]) == (2, 1)
    assert rows["fx-root.1"]["phase"] == "verify"
    assert (rows["fx-root.1"]["ready"], rows["fx-root.4"]["ready"]) == (True, False)
    assert "ready" not in rows["fx-root"]
    assert document["asks"][0]["question"] == "ship it?"
    assert document["asks"][0]["waiting_s"] > 0
    assert document["asks"][0]["actions"] == [
        {"offer": "Approve it", "basicly": "checkpoint-approve"}
    ]
    assert document["session"]["token_budget"] == 80000000
    assert (document["session"]["grant_level"], document["session"]["spent_tokens"]) == ("L3", 12)
    assert board_schema.verdict(board_repo, document).exit_code == 0


def test_no_absolute_path_or_username_reaches_the_document(board_repo: Path) -> None:
    facts = board_snapshot.SessionFacts(root_issue="fx-root", session_id="/home/someone/lock")
    _run_records(board_repo, {"fx-root.1": [_dispatch(command=["claude", "/home/someone/x"])]})
    rendered = json.dumps(_built(board_repo, facts=board_snapshot.Facts(session=facts), now=NOW))
    assert "/home/someone" not in rendered
    assert "C:\\Users" not in rendered


def test_an_unreadable_ledger_costs_the_tracker_sections_and_not_the_document(
    tmp_path: Path,
) -> None:
    document = _built(tmp_path, now=NOW)
    assert set(document) == {"schema", "generated_at", "freshness", "generator", "repo"}
    assert document["schema"] == board_schema.VERSION
    assert document["freshness"]["source"] == board_snapshot.ONE_SHOT


def test_a_caller_on_a_tick_declares_its_own_cadence(board_repo: Path) -> None:
    freshness = board_snapshot.Freshness(source="supervisor-tick", cadence_s=15, stale_after_s=60)
    document = _built(board_repo, freshness=freshness, now=NOW)
    assert document["freshness"] == {
        "source": "supervisor-tick",
        "cadence_s": 15,
        "stale_after_s": 60,
    }
    assert board_schema.verdict(board_repo, document).exit_code == 0


def test_a_relative_repo_root_still_names_the_repo(board_repo: Path, monkeypatch) -> None:

    monkeypatch.chdir(board_repo)
    relative = board_snapshot.build_document(Path())
    absolute = board_snapshot.build_document(board_repo.resolve())

    assert relative["repo"] == absolute["repo"]
    assert relative["repo"] == {"name": board_repo.resolve().name}
    assert board_schema.verdict(board_repo, relative).exit_code == 0

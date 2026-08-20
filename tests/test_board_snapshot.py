"""Tests for the file-only board snapshot producer (basicly-rn0o.2).

Every count here is pinned against the frozen corpus under `tests/fixtures/board/ledger/`,
copied over the work repo's own log. Pinning against this checkout's ledger would be a gate
that goes red on the next landing - it is git-tracked and grew from 980 records to 984 across
two sessions - so the live tree is used only where the assertion is a *bound* rather than a
count: the build-time cap, and the schema verdict.

The two claims that need an instrument rather than an assertion are the fold count and the
subprocess count. Both are spied, because "reads only files" and "folds once" are exactly the
properties one convenience import restores to false while every other test stays green.
"""

from __future__ import annotations

import ast
import json
import shutil
import subprocess
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest

from basicly import (
    board_fields,
    board_schema,
    board_snapshot,
    owned_store,
    run_record,
    verify_artifact,
)

REPO_ROOT = Path(__file__).parent.parent
FIXTURE_LEDGER = REPO_ROOT / "tests" / "fixtures" / "board" / "ledger" / "events-0001.jsonl"
MINIMAL = REPO_ROOT / "tests" / "fixtures" / "board" / "minimal-v1.json"

# AC 4's cap. 26x the 19.1 ms measured on this corpus, so it fails on a regression and not
# on a slow runner; the ratio is deliberate and is not a tight band.
BUILD_CAP_S = 0.5

NOW = datetime(2026, 1, 2, tzinfo=UTC)

# What the frozen corpus holds: seven records, one of them tombstoned and therefore absent.
FIXTURE_TOTAL = 6
FIXTURE_CLOSED = 2
FIXTURE_IN_PROGRESS = 1


@pytest.fixture
def board_repo(work_repo: Path) -> Path:
    """A work repo whose ledger is the frozen board corpus and whose usage dir is absent."""
    ledger = owned_store.ledger_dir(work_repo)
    ledger.mkdir(parents=True, exist_ok=True)
    for stale in ledger.glob("events-*.jsonl"):
        stale.unlink()
    shutil.copy2(FIXTURE_LEDGER, ledger / "events-0001.jsonl")
    return work_repo


def _built(repo_root: Path, **kwargs: Any) -> dict[str, Any]:
    """The document, typed for indexing. `build_document` returns `dict[str, object]`.

    Deliberately narrow rather than loosening the producer's own annotation: a section is a
    heterogeneous JSON value there, and a caller that indexes one is asserting a shape the
    schema already rules on.
    """
    return cast("dict[str, Any]", board_snapshot.build_document(repo_root, **kwargs))


def _run_records(repo_root: Path, records: dict) -> None:
    """Write a run-record file, the source `spend` and `health` are omitted without."""
    path = repo_root / run_record.RUN_RECORDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(records), encoding="utf-8")


def _dispatch(**overrides: object) -> dict:
    """One dispatch entry in the shape `run_record` persists."""
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
    """The demonstration, as an assertion: the producer's own output rules conformant.

    The refusal half is the control. `board validate` exits 0 on the shipped minimal
    fixture too, so a passing verdict on its own would not distinguish a producer that
    works from a validator that admits everything - removing a required key must refuse.
    """
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
    """AC 1, instrumented. `observe()` folds the same log 93 times; this folds it once."""
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


def test_a_build_on_this_repos_corpus_stays_under_the_cap() -> None:
    """AC 4, against the live tree, because the cap is a bound and not a count."""
    samples = []
    for _ in range(5):
        started = time.perf_counter()
        board_snapshot.build_document(REPO_ROOT)
        samples.append(time.perf_counter() - started)
    assert sorted(samples)[2] < BUILD_CAP_S, samples


def test_the_session_section_is_omitted_until_the_caller_supplies_the_lock_facts(
    board_repo: Path,
) -> None:
    """AC 2 and AC 3: absent rather than nulls, and never a guessed root."""
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
    """basicly-06pvsc: caller-supplied or omitted, and nothing in between.

    The omission is the half that matters. `lanes[].phase` is required and its authority
    reads a source this producer does not open, so a derived phase would be the estimate
    the contract forbids - and an *empty* section would claim the caller can see lanes.
    """
    assert "lanes" not in _built(board_repo, now=NOW)

    empty = _built(board_repo, facts=board_snapshot.Facts(lanes=[]), now=NOW)
    assert empty["lanes"] == []
    assert board_schema.verdict(board_repo, empty).exit_code == 0

    supplied = board_fields.LaneFacts(id="fx-root.1", phase="verify", agent="claude", live=True)
    document = _built(board_repo, facts=board_snapshot.Facts(lanes=[supplied]), now=NOW)
    assert document["lanes"] == [
        {"id": "fx-root.1", "phase": "verify", "agent": "claude", "live": True}
    ]
    assert board_schema.verdict(board_repo, document).exit_code == 0


def test_a_holder_the_caller_could_not_read_leaves_the_triple_out(board_repo: Path) -> None:
    """No lock held is not a holder with an empty id: the key is absent."""
    facts = board_snapshot.SessionFacts(root_issue="fx-root")
    section = _built(board_repo, facts=board_snapshot.Facts(session=facts), now=NOW)["session"]
    assert "holder" not in section
    assert section["supervised"] is False


@pytest.mark.parametrize("module", ["board_snapshot", "board_fields"])
def test_the_producer_does_not_import_supervise(module: str) -> None:
    """AC 2's structural half, read off the import statements rather than off the prose.

    Unit F has `supervise` import this module, so the reverse edge is the cycle
    `supervise -> board_snapshot -> supervise` that C11 rules out. `.importlinter` already
    holds the tier; this names the one edge, so the reason survives a tier being moved.
    """
    tree = ast.parse((REPO_ROOT / "src" / "basicly" / f"{module}.py").read_text(encoding="utf-8"))
    imported = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            imported.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            imported.update(alias.name for alias in node.names)
            imported.add(node.module or "")
    assert "supervise" not in imported
    assert not any(name.endswith(".supervise") for name in imported)


def test_spend_and_health_are_omitted_while_no_run_records_exist(board_repo: Path) -> None:
    """AC 7: in a lane worktree `.basicly/usage/` does not exist, and the rest still builds."""
    document = _built(board_repo, now=NOW)
    assert "spend" not in document
    assert "health" not in document
    assert document["backlog"]["closed"] == FIXTURE_CLOSED


def test_spend_and_health_arrive_with_the_run_records(board_repo: Path) -> None:
    """The other half of AC 7, so the omission above is a state and not a dead branch."""
    _run_records(board_repo, {"fx-root.1": [_dispatch(), _dispatch(cost=5.0)]})
    document = _built(board_repo, now=NOW)
    assert document["spend"] == {
        "scope": board_snapshot.MACHINE_LOCAL,
        "lifetime_usd": 7.0,
        "largest_dispatch_usd": 5.0,
        "input_tokens": 20,
        "output_tokens": 40,
        "cache_read_tokens": 60,
        "cache_write_tokens": 80,
    }
    assert [row["agent"] for row in document["health"]] == ["claude"]
    assert document["health"][0]["runs"] == 2


def test_an_estimated_dispatch_is_left_out_of_spend(board_repo: Path) -> None:
    """AC 10: the schema cannot mark a value as estimated, so an estimate is not emitted."""
    _run_records(board_repo, {"fx-root.1": [_dispatch(), _dispatch(cost=99.0, estimated=True)]})
    assert _built(board_repo, now=NOW)["spend"]["lifetime_usd"] == 2.0


def test_spend_is_omitted_where_every_dispatch_is_an_estimate(board_repo: Path) -> None:
    """The declared limit for the copilot cells, where dropping estimates drops everything."""
    _run_records(board_repo, {"fx-root.1": [_dispatch(estimated=True)]})
    document = _built(board_repo, now=NOW)
    assert "spend" not in document
    assert "health" in document


def test_the_gates_section_comes_from_the_verify_artifact(board_repo: Path) -> None:
    """And a `skip` becomes `not_run`, because a value outside a closed set is refused."""
    artifact = board_repo / verify_artifact.RUN_ARTIFACT
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        json.dumps({
            "mode": "fast",
            "recorded_at": "2026-01-01T00:00:00+00:00",
            "passed": True,
            "checks": [
                {"name": "ruff", "status": "pass"},
                {"name": "docs", "status": "skip"},
                {"name": "invented", "status": "wat"},
            ],
        }),
        encoding="utf-8",
    )
    section = _built(board_repo, now=NOW)["gates"]
    assert section["mode"] == "fast"
    assert section["passed"] is True
    assert section["checks"] == [
        {"name": "ruff", "status": "pass"},
        {"name": "docs", "status": "not_run"},
    ]


def test_the_backlog_and_the_ask_pin_the_frozen_corpus(board_repo: Path) -> None:
    """One tombstone dropped, and one pending ask out of 140 request markers."""
    document = _built(board_repo, now=NOW)
    assert document["backlog"] == {
        "total": FIXTURE_TOTAL,
        "active": FIXTURE_TOTAL - FIXTURE_CLOSED,
        "in_progress": FIXTURE_IN_PROGRESS,
        "closed": FIXTURE_CLOSED,
        "by_priority": {"P0": 1, "P1": 2, "P2": 2, "P3": 1},
    }
    assert [ask["wait_id"] for ask in document["asks"]] == ["fx-root.1#wait-ship"]
    assert len(document["events"]) == board_snapshot.EVENT_LIMIT


def test_no_absolute_path_or_username_reaches_the_document(board_repo: Path) -> None:
    """AC 6, on the two surfaces that carry one: a caller's facts and a dispatch command."""
    facts = board_snapshot.SessionFacts(root_issue="fx-root", session_id="/home/someone/lock")
    _run_records(board_repo, {"fx-root.1": [_dispatch(command=["claude", "/home/someone/x"])]})
    rendered = json.dumps(_built(board_repo, facts=board_snapshot.Facts(session=facts), now=NOW))
    assert "/home/someone" not in rendered
    assert "C:\\Users" not in rendered


def test_an_unreadable_ledger_costs_the_tracker_sections_and_not_the_document(
    tmp_path: Path,
) -> None:
    """A repo with no kit installed still produces a conformant three-key document."""
    document = _built(tmp_path, now=NOW)
    assert set(document) == {"schema", "generated_at", "freshness", "generator", "repo"}
    assert document["schema"] == board_schema.VERSION
    assert document["freshness"]["source"] == board_snapshot.ONE_SHOT


def test_a_caller_on_a_tick_declares_its_own_cadence(board_repo: Path) -> None:
    """`freshness` is how old the document is allowed to get, and the caller owns it."""
    freshness = board_snapshot.Freshness(source="supervisor-tick", cadence_s=15, stale_after_s=60)
    document = _built(board_repo, freshness=freshness, now=NOW)
    assert document["freshness"] == {
        "source": "supervisor-tick",
        "cadence_s": 15,
        "stale_after_s": 60,
    }
    assert board_schema.verdict(board_repo, document).exit_code == 0


def test_a_relative_repo_root_still_names_the_repo(board_repo: Path, monkeypatch) -> None:
    """`repo.name` may not depend on how the caller spelled the path.

    Found by validating the shipped producer as a consumer does, from the repository
    root, where `build_document(Path("."))` is the obvious call: `Path(".").name` is
    `""`, the schema refuses an empty name, and the whole `repo` section was withheld
    with exit 3 while every other section rendered. The worktree demonstration missed
    it because it passed an absolute path. Resolving before taking the last component
    is the fix; asserting the relative spelling is what keeps it fixed.
    """
    monkeypatch.chdir(board_repo)
    relative = board_snapshot.build_document(Path())
    absolute = board_snapshot.build_document(board_repo.resolve())

    assert relative["repo"] == absolute["repo"]
    assert relative["repo"] == {"name": board_repo.resolve().name}
    assert board_schema.verdict(board_repo, relative).exit_code == 0

# module-size-waiver: cost(basicly-k6tpep.2): 4796 of 4000 tokens. Four cases for the pass

from __future__ import annotations

import json
import shutil
import subprocess  # nosec B404
from pathlib import Path

import pytest

from basicly import (
    board_facts,
    board_regions,
    board_schema,
    board_sections,
    board_snapshot,
    integrity,
    loop_state,
    supervise,
    tracker,
)
from basicly.config import VERIFY_GATE_PROVIDER

REPO_ROOT = Path(__file__).parent.parent
KIT_SOURCE = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


def _git(cwd: Path, *args: str) -> None:
    subprocess.run(["git", *args], cwd=cwd, check=True, capture_output=True, text=True)  # nosec B603 B607


@pytest.fixture
def git_repo(tmp_path: Path) -> Path:

    repo = tmp_path / "repo"
    repo.mkdir()
    _git(repo, "init", "-b", "probe")
    _git(repo, "config", "user.name", "Test")
    _git(repo, "config", "user.email", "test@example.com")
    (repo / "README.md").write_text("hi\n", encoding="utf-8")
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "init")
    return repo


def _owned_repo(root: Path, *records: str) -> Path:

    (root / tracker.KIT_TRACKER_DIR).mkdir(parents=True, exist_ok=True)
    for source in sorted(KIT_SOURCE.glob("*.py")):
        shutil.copy2(source, root / tracker.KIT_TRACKER_DIR / source.name)
    (root / tracker.LEDGER_DIR).mkdir(parents=True, exist_ok=True)
    (root / "basicly.toml").write_text('[tracker]\nmode = "owned"\n', encoding="utf-8")
    kit = tracker.kit(root)
    kit.events.append(
        tracker.ledger_dir(root),
        [
            kit.events.Draft(record, kit.events.KIND_STATUS, {"status": "open"})
            for record in records
        ],
    )
    return root


def _seeded(repo: Path, *, level: str) -> Path:

    if level:
        tracker.add_comment(repo, "bd-1", f"{integrity.CLASSIFICATION_MARKER} level={level}")
    tracker.write(
        repo,
        ["update", "bd-1", "--external-ref", loop_state.format_worktree_ref("w", "harness/w")],
    )
    tracker.write(
        repo,
        [
            "gate",
            "report",
            "bd-1",
            "--gate",
            "verify",
            "--provider",
            VERIFY_GATE_PROVIDER,
            "--status",
            "pass",
        ],
    )
    return repo


def test_no_supervisor_lock_yields_no_session_facts(tmp_path: Path) -> None:
    assert board_facts.session_facts(tmp_path) is None


def test_no_grant_yields_no_grant(tmp_path: Path) -> None:
    assert board_facts.active_grant(tmp_path, "demo-1") is None


def test_no_grant_yields_no_spend(tmp_path: Path) -> None:
    assert board_facts.grant_spend(tmp_path, "demo-1", None) is None


def test_an_unreadable_tracker_yields_no_readiness(tmp_path: Path) -> None:
    assert board_facts.readiness(tmp_path) is None


def test_an_unreadable_tracker_yields_no_phases(tmp_path: Path) -> None:
    assert board_facts.phases(tmp_path) == {}


def test_a_document_with_no_asks_yields_no_questions(tmp_path: Path) -> None:
    assert board_facts.questions(tmp_path, {}) == {}


def test_the_session_section_is_omitted_rather_than_guessed(tmp_path: Path) -> None:
    assert board_facts.session_facts(tmp_path) is None


def test_a_held_lock_supplies_the_session_facts_the_producer_may_not_derive(tmp_path: Path) -> None:

    lock = tmp_path / supervise.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "session_id": "abc", "root_issue": "x-1"}))
    facts = board_facts.session_facts(tmp_path)
    assert facts is not None
    assert facts.root_issue == "x-1"
    assert facts.session_id == "abc"
    assert facts.supervised is True
    assert facts.stale is False


def test_no_live_lock_emits_an_empty_lane_section_rather_than_no_section(tmp_path: Path) -> None:

    assert board_facts.document(tmp_path)["lanes"] == []


def test_a_live_lock_keeps_the_lane_section_absent(tmp_path: Path) -> None:

    lock = tmp_path / supervise.LOCK_FILE
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "session_id": "abc", "root_issue": "x-1"}))

    assert "lanes" not in board_facts.document(tmp_path)


def test_live_grant_spend_is_absent_then_advances_and_rides_named_apart(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    assert board_facts.live_grant_spend(board_snapshot.SessionFacts(root_issue="x-1")) is None
    session = board_snapshot.SessionFacts(root_issue="x-1", token_budget=1_000_000, spent_tokens=10)
    assert board_facts.live_grant_spend(session) is None
    built: dict[str, object] = {"session": {"spent_tokens": 10}}
    monkeypatch.setattr(supervise, "inflight_spend", lambda: {"x-1.1": 200})
    first = board_facts.live_grant_spend(session)
    monkeypatch.setattr(supervise, "inflight_spend", lambda: {"x-1.1": 500})
    second = board_facts.live_grant_spend(session)
    assert (first, second) == (210, 510)
    section = board_facts._with_live_spend(built, session)["session"]
    assert isinstance(section, dict)
    assert (section["spent_tokens"], section["spent_tokens_live"]) == (10, 510)
    assert section["spent_tokens_live_over_estimate"] is True
    assert section["spent_tokens_live_bound"] == supervise.LIVE_OVERREPORT_BOUND


def test_the_git_state_the_producer_may_not_read_comes_from_this_layer(git_repo: Path) -> None:

    clean = board_facts.repo_facts(git_repo)
    assert clean is not None
    assert clean.branch == "probe"
    assert clean.dirty is False
    assert len(clean.head) >= 7

    (git_repo / "README.md").write_text("changed\n", encoding="utf-8")
    dirty = board_facts.repo_facts(git_repo)
    assert dirty is not None
    assert dirty.dirty is True
    assert dirty.head == clean.head


def test_a_directory_git_will_not_answer_for_keeps_the_repo_section_to_its_name(
    tmp_path: Path,
) -> None:
    assert board_facts.repo_facts(tmp_path) is None


def test_the_phase_map_covers_every_record_and_folds_the_log_once(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    records = tuple(f"bd-{index}" for index in range(11))
    repo = _owned_repo(tmp_path, *records)
    kit = tracker.kit(repo)
    folds: list[int] = []
    real_fold = kit.events.fold

    def counting_fold(*args: object, **kwargs: object) -> object:
        folds.append(1)
        return real_fold(*args, **kwargs)

    def refuse(*_args: object, **_kwargs: object) -> None:
        raise AssertionError("the phase map spawned a subprocess")

    monkeypatch.setattr(kit.events, "fold", counting_fold)
    monkeypatch.setattr(subprocess, "Popen", refuse)

    phases = board_facts.phases(repo)

    assert len(folds) == 1
    assert set(phases) == set(records)
    assert all(value for value in phases.values())


def test_a_mapped_phase_is_the_engine_derivation_on_a_unit_owing_validation(
    tmp_path: Path,
) -> None:

    l3 = _seeded(_owned_repo(tmp_path / "l3", "bd-1"), level="L3")
    l2 = _seeded(_owned_repo(tmp_path / "l2", "bd-1"), level="")

    assert loop_state.phase_map(l3)["bd-1"] == "validate"
    assert loop_state.phase_map(l2)["bd-1"] == "verify"
    for repo in (l3, l2):
        assert loop_state.phase_map(repo)["bd-1"] == loop_state.read_node_state(repo, "bd-1").phase


def _ask(kind: str, issue: str, subject: str = "") -> dict[str, str]:
    row = {"wait_id": f"{issue}#wait-{subject or 'x'}", "issue": issue, "kind": kind}
    return {**row, "subject": subject} if subject else row


def test_visible_asks_matches_each_acceptance_case() -> None:
    filt, both = board_facts._visible_asks, frozenset({"bd-1", "bd-2"})
    classify, decision = _ask("checkpoint", "bd-1", "classify"), _ask("decision", "bd-2")
    assert [a["issue"] for a in filt([classify, decision], both, "L3")] == ["bd-2"]
    assert filt([classify], frozenset({"bd-2"}), "") == []
    assert filt([classify], both, "L1") == [classify]
    assert filt([decision], both, "L3") == [decision]


def test_hide_unanswerable_reads_the_grant_and_the_units_off_the_document() -> None:
    built = {
        "asks": [_ask("checkpoint", "bd-1", "classify"), _ask("decision", "bd-2")],
        "units": [{"id": "bd-1"}, {"id": "bd-2"}],
        "session": {"grant_level": "L3"},
    }
    board_facts._hide_unanswerable(built)
    assert [a["issue"] for a in built["asks"]] == ["bd-2"]
    assert board_facts._hide_unanswerable({"units": []}) == {"units": []}


_DISPATCHED = {
    "agent": "codex",
    "model": "gpt-6",
    "cost": 12.5,
    "duration_s": 900.0,
}


def _running(issue_id: str) -> supervise.LaneView:
    return supervise.LaneView(
        issue_id=issue_id,
        status="in_progress",
        worktree=issue_id,
        branch=f"harness/{issue_id}",
        live=True,
        last_agent="codex",
        last_run_at="2026-08-26T17:00:00Z",
    )


def test_a_running_lane_names_the_dispatch_it_is_running_not_the_one_before_it() -> None:

    meter = supervise.LaneStream(agent="claude", model="claude-opus-5")
    fact = board_facts._lane_fact(
        _running("a"), {"a": "build"}, {"a": 7}, {}, [_DISPATCHED], dispatch={"a": meter}
    )
    assert (fact.agent, fact.model) == ("claude", "claude-opus-5")
    assert fact.started_at == meter.started_at != "2026-08-26T17:00:00Z"
    assert fact.elapsed_s is not None
    assert fact.elapsed_s < 900.0, "the last run's duration is not this dispatch's elapsed"


def test_a_running_lane_with_no_run_record_at_all_still_names_its_dispatch() -> None:
    fact = board_facts._lane_fact(
        supervise.LaneView("a", "in_progress", "a", "harness/a", True),
        {"a": "build"},
        {"a": 0},
        {},
        [],
        dispatch={"a": supervise.LaneStream(agent="claude", model="claude-opus-5")},
    )
    assert (fact.agent, fact.model) == ("claude", "claude-opus-5")
    assert fact.started_at and fact.elapsed_s is not None
    assert (fact.cost_usd, fact.context_used) == (None, None)


def test_a_meter_naming_no_runner_publishes_nothing_rather_than_an_empty_string() -> None:
    fact = board_facts._lane_fact(
        _running("a"),
        {"a": "build"},
        {"a": 7},
        {},
        [_DISPATCHED],
        dispatch={"a": supervise.LaneStream()},
    )
    assert (fact.agent, fact.model) == ("codex", "gpt-6")


def _standing(state: str, detail: str = "") -> supervise.LaneStanding:
    return supervise.LaneStanding(state, detail, "2026-08-26T17:00:00Z")


def _bound(issue_id: str) -> supervise.LaneView:
    return supervise.LaneView(
        issue_id=issue_id,
        status="in_progress",
        worktree=issue_id,
        branch=f"harness/{issue_id}",
        live=True,
        last_agent="claude",
    )


def test_the_closed_state_set_is_spelled_the_same_in_all_three_places() -> None:

    schema = json.loads(
        (REPO_ROOT / ".basicly" / "core" / "schemas" / board_schema.SCHEMA_FILE).read_text(
            encoding="utf-8"
        )
    )
    declared = set(schema["properties"]["lanes"]["items"]["properties"]["state"]["enum"])
    written = {
        supervise.LANE_QUEUED,
        supervise.LANE_RUNNING,
        supervise.LANE_WAITS_TO_LAND,
        supervise.LANE_LANDING,
        supervise.LANE_LANDED,
        supervise.LANE_REFUSED,
        supervise.LANE_PARKED,
    }
    assert declared == board_sections.LANE_STATES
    assert declared == written
    assert declared == set(board_regions.LANE_MARKS), "a state the wall cannot draw"


def test_a_published_standing_reaches_the_row_with_its_reason_and_its_stamp() -> None:

    fact = board_facts._lane_fact(
        _bound("a"),
        {"a": "build"},
        {},
        {},
        [],
        standing=_standing("refused", "downstream WIP bound 5 reached"),
    )
    assert (fact.state, fact.state_detail) == ("refused", "downstream WIP bound 5 reached")
    assert fact.state_since == "2026-08-26T17:00:00Z"
    assert board_sections.lanes([fact])[0]["state"] == "refused"


def test_a_live_stream_outranks_a_standing_the_pass_has_not_retired() -> None:

    fact = board_facts._lane_fact(
        _bound("a"), {"a": "build"}, {"a": 7}, {}, [], standing=_standing("queued", "waiting")
    )
    assert (fact.state, fact.state_detail, fact.state_since) == ("running", "", "")


def test_a_lane_with_no_standing_and_no_stream_names_no_state_at_all() -> None:
    fact = board_facts._lane_fact(_bound("a"), {"a": "build"}, {}, {}, [])
    assert (fact.state, fact.state_detail, fact.state_since) == ("", "", "")
    assert "state" not in board_sections.lanes([fact])[0]

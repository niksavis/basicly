from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import context_meter, run_record, runner, tracker
from basicly.config import load_sizing_config
from basicly.run_record import (
    EXECUTED,
    FAILED,
    HANDOFF,
    REDACTED_PROMPT,
    RUN_RECORDS_FILE,
    RunRecord,
)
from tests import fake_tracker, flipped_tracker


def _records(repo_root: Path) -> dict:
    return json.loads((repo_root / RUN_RECORDS_FILE).read_text(encoding="utf-8"))


def test_outcome_of_labels_handoff_executed_and_failed() -> None:
    assert run_record.outcome_of(handoff=True, returncode=None) == HANDOFF
    assert run_record.outcome_of(handoff=False, returncode=0) == EXECUTED
    assert run_record.outcome_of(handoff=False, returncode=2) == FAILED
    assert run_record.outcome_of(handoff=False, returncode=None) == FAILED


def test_build_record_derives_outcome_stamps_time_and_defaults_fields() -> None:
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.5,
        command=("claude", "-p", REDACTED_PROMPT),
    )
    assert entry.agent == "claude"
    assert entry.outcome == EXECUTED
    assert entry.duration_s == 1.5
    assert entry.timestamp
    assert entry.model is None and entry.tokens is None and entry.cost is None
    assert entry.estimated is None


def test_build_record_stamps_model_provenance() -> None:
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude", "--model", "opus", "-p", REDACTED_PROMPT),
        model="opus",
    )
    assert entry.model == "opus"
    assert entry.tokens is None and entry.cost is None


def test_build_record_carries_token_telemetry(tmp_path: Path) -> None:
    reported = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", REDACTED_PROMPT, "--output-format", "json"),
        tokens=21475,
        cost=0.136147,
        estimated=False,
    )
    estimated = run_record.build_record(
        agent="copilot",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("copilot", "-p", REDACTED_PROMPT),
        tokens=30,
        estimated=True,
    )
    run_record.record(tmp_path, "i", reported)
    run_record.record(tmp_path, "i", estimated)

    first, second = _records(tmp_path)["i"]
    assert (first["tokens"], first["cost"], first["estimated"]) == (21475, 0.136147, False)
    assert (second["tokens"], second["cost"], second["estimated"]) == (30, None, True)
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None
    assert (latest.tokens, latest.cost, latest.estimated) == (30, None, True)


def test_build_record_carries_the_token_split_and_credits(tmp_path: Path) -> None:

    entry = run_record.build_record(
        agent="copilot",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("copilot", "-p", REDACTED_PROMPT, "--session-id", "sid-1"),
        tokens=24214,
        estimated=False,
        input_tokens=24210,
        output_tokens=4,
        cache_read_tokens=0,
        cache_write_tokens=24208,
        reasoning_tokens=0,
        credits=6.0564,
    )
    run_record.record(tmp_path, "i", entry)

    stored = _records(tmp_path)["i"][0]
    assert stored["tokens"] == 24214
    assert (stored["input_tokens"], stored["output_tokens"]) == (24210, 4)
    assert (stored["cache_read_tokens"], stored["cache_write_tokens"]) == (0, 24208)
    assert stored["reasoning_tokens"] == 0
    assert stored["credits"] == 6.0564
    assert stored["cost"] is None
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None
    assert (latest.credits, latest.cache_write_tokens) == (6.0564, 24208)


def test_build_record_leaves_the_split_null_for_a_splitless_adapter(tmp_path: Path) -> None:
    entry = run_record.build_record(
        agent="codex",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("codex", "exec", REDACTED_PROMPT, "--json"),
        tokens=24892,
        estimated=False,
    )
    run_record.record(tmp_path, "i", entry)
    stored = _records(tmp_path)["i"][0]
    assert stored["tokens"] == 24892
    assert stored["input_tokens"] is None and stored["credits"] is None


def test_record_writes_keyed_list_and_self_ignores(tmp_path: Path) -> None:
    entry = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.1, command=("claude",)
    )
    run_record.record(tmp_path, "basicly-z6dh.1", entry)

    data = _records(tmp_path)
    assert list(data) == ["basicly-z6dh.1"]
    assert data["basicly-z6dh.1"][0]["outcome"] == EXECUTED
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_record_accumulates_history_per_bead(tmp_path: Path) -> None:
    first = run_record.build_record(
        agent="claude", handoff=False, returncode=2, duration_s=0.1, command=("claude",)
    )
    second = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.2, command=("claude",)
    )
    run_record.record(tmp_path, "i", first)
    run_record.record(tmp_path, "i", second)

    history = _records(tmp_path)["i"]
    assert [r["outcome"] for r in history] == [FAILED, EXECUTED]


def test_record_never_persists_the_raw_prompt(tmp_path: Path) -> None:
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=0.1,
        command=("claude", "-p", REDACTED_PROMPT),
    )
    run_record.record(tmp_path, "i", entry)
    blob = (tmp_path / RUN_RECORDS_FILE).read_text(encoding="utf-8")
    assert REDACTED_PROMPT in blob
    assert "implement the work" not in blob


def test_record_restarts_on_a_corrupt_file(tmp_path: Path) -> None:
    (tmp_path / ".basicly/usage").mkdir(parents=True)
    (tmp_path / RUN_RECORDS_FILE).write_text("{ not json", encoding="utf-8")
    entry = run_record.build_record(
        agent="codex", handoff=True, returncode=None, duration_s=None, command=()
    )
    run_record.record(tmp_path, "i", entry)
    assert _records(tmp_path)["i"][0]["outcome"] == HANDOFF


def test_record_restarts_on_a_wrong_shaped_value(tmp_path: Path) -> None:
    (tmp_path / ".basicly/usage").mkdir(parents=True)
    (tmp_path / RUN_RECORDS_FILE).write_text('{"i": "tampered"}', encoding="utf-8")
    entry = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.1, command=("claude",)
    )
    run_record.record(tmp_path, "i", entry)
    assert _records(tmp_path)["i"][0]["outcome"] == EXECUTED


def test_load_run_records_none_when_absent(tmp_path: Path) -> None:
    assert run_record.load_run_records(tmp_path) is None


def test_load_run_records_round_trips(tmp_path: Path) -> None:
    entry = RunRecord(
        agent="copilot",
        outcome=EXECUTED,
        returncode=0,
        duration_s=3.0,
        command=("copilot", "-p", REDACTED_PROMPT),
        timestamp="2026-07-17T00:00:00+00:00",
    )
    run_record.record(tmp_path, "i", entry)
    loaded = run_record.load_run_records(tmp_path)
    assert loaded is not None
    assert loaded["i"][0]["agent"] == "copilot"
    assert loaded["i"][0]["command"] == ["copilot", "-p", REDACTED_PROMPT]


def test_a_dispatch_records_the_occupancy_and_the_window_its_ceiling_came_from(
    tmp_path: Path,
) -> None:

    stale = replace(
        next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == "claude"),
        context_window=200_000,
    )
    entry = run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.0,
        command=("claude", "-p", REDACTED_PROMPT),
        context_tokens=145_570,
        context_window=stale.context_window,
        context_window_source=runner.ADAPTER_WINDOW,
    )
    run_record.record(tmp_path, "i", entry)

    persisted = _records(tmp_path)["i"][0]
    assert persisted["context_tokens"] == 145_570
    assert persisted["context_window"] == 200_000
    assert persisted["context_window_source"] == runner.ADAPTER_WINDOW
    ceiling = context_meter.ceiling_tokens(stale, load_sizing_config(tmp_path))
    assert ceiling == 120_000
    assert persisted["context_tokens"] > ceiling


def test_latest_record_none_when_absent(tmp_path: Path) -> None:
    assert run_record.latest_record(tmp_path, "i") is None


def test_latest_record_returns_the_most_recent_with_model(tmp_path: Path) -> None:
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", REDACTED_PROMPT),
        ),
    )
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="codex",
            handoff=False,
            returncode=0,
            duration_s=2.0,
            command=("codex", "exec", REDACTED_PROMPT),
            model="o4",
        ),
    )
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None
    assert latest.agent == "codex" and latest.model == "o4"


def test_latest_record_tolerates_an_unknown_key(tmp_path: Path) -> None:
    run_record.record(
        tmp_path,
        "i",
        run_record.build_record(
            agent="claude",
            handoff=False,
            returncode=0,
            duration_s=1.0,
            command=("claude", "-p", REDACTED_PROMPT),
        ),
    )
    records_file = tmp_path / run_record.RUN_RECORDS_FILE
    data = json.loads(records_file.read_text(encoding="utf-8"))
    data["i"][0]["future_field"] = "xyz"
    records_file.write_text(json.dumps(data), encoding="utf-8")
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None and latest.agent == "claude"


def test_marker_id_is_content_derived_and_attempt_aware() -> None:
    first = run_record.marker_id("basicly-x", "abc", "build")
    assert first == run_record.marker_id("basicly-x", "abc", "build")
    assert run_record.marker_id("basicly-x", "abc", "build", 2) != first
    assert run_record.marker_id("basicly-x", "abc", "validate") != first
    assert first.startswith("basicly-x#run-")


def _entry(**kw) -> run_record.RunRecord:
    return run_record.build_record(
        agent="claude",
        handoff=False,
        returncode=0,
        duration_s=1.5,
        command=("claude",),
        **kw,
    )


def test_record_marker_writes_one_marker_carrying_the_dispatch_inputs(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    calls: list[list[str]] = []

    def _try_run_br(_repo, args):
        calls.append(args)
        if args[:2] == ["comments", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        return SimpleNamespace(returncode=0, stdout="")

    fake_tracker.install(monkeypatch, _try_run_br)
    entry = _entry(
        model="claude-opus-5",
        adapter_version="2.1.4",
        prompt_sha256="deadbeef",
        phase="build",
        scope_tokens=8123,
        forecast_tokens=24000,
        folded_info=("basicly-y#coupling-1234abcd",),
        tokens=21300,
        cost=0.42,
    )
    ident = run_record.record_marker(tmp_path, "basicly-x", entry)

    assert ident == run_record.marker_id("basicly-x", "deadbeef", "build")
    add = next(c for c in calls if c[:2] == ["comments", "add"])
    header, payload = add[3].split("\n", 1)
    assert header == f"{run_record.MARKER} id={ident} phase=build"
    body = json.loads(payload)
    assert body["adapter_version"] == "2.1.4"
    assert body["prompt_sha256"] == "deadbeef"
    assert body["scope_tokens"] == 8123
    assert body["forecast_tokens"] == 24000
    assert body["folded_info"] == ["basicly-y#coupling-1234abcd"]
    assert body["cost"] == 0.42
    assert "prompt" not in body


def test_record_marker_carries_the_dispatch_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def _try_run_br(_repo, args):
        if args[:2] == ["comments", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        _try_run_br.added = args  # type: ignore[attr-defined]
        return SimpleNamespace(returncode=0, stdout="")

    fake_tracker.install(monkeypatch, _try_run_br)
    entry = _entry(
        prompt_sha256="deadbeef",
        phase="lane",
        dispatch_rank=2,
        scheduler_rank=1,
        scheduler_fallback_rank=3,
        scheduler_score=45,
        scheduler_policy="tracker.scheduler.v1",
    )
    run_record.record_marker(tmp_path, "basicly-x", entry)

    body = json.loads(_try_run_br.added[3].split("\n", 1)[1])  # type: ignore[attr-defined]
    assert body["dispatch_rank"] == 2
    assert body["scheduler_rank"] == 1
    assert body["scheduler_fallback_rank"] == 3
    assert body["scheduler_score"] == 45
    assert body["scheduler_policy"] == "tracker.scheduler.v1"


def test_build_record_defaults_the_ordering_to_unrecorded(tmp_path: Path) -> None:
    _ = tmp_path
    entry = run_record.build_record(
        agent="codex", handoff=False, returncode=0, duration_s=1.0, command=("codex",)
    )
    assert entry.dispatch_rank is None
    assert entry.scheduler_policy is None


def test_record_marker_is_idempotent_but_counts_a_real_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    recorded: list[str] = []

    def _try_run_br(_repo, args):
        if args[:2] == ["comments", "list"]:
            texts = [{"text": t} for t in recorded]
            return SimpleNamespace(returncode=0, stdout=json.dumps(texts))
        if args[:2] == ["comments", "add"]:
            recorded.append(args[3])
        return SimpleNamespace(returncode=0, stdout="")

    fake_tracker.install(monkeypatch, _try_run_br)
    entry = _entry(prompt_sha256="cafe", phase="build")
    first = run_record.record_marker(tmp_path, "basicly-x", entry)
    second = run_record.record_marker(tmp_path, "basicly-x", entry)

    assert first != second, "a second run must not collapse into the first"
    assert second is not None and second.endswith("-2")
    assert len(recorded) == 2


def test_record_marker_skips_when_there_is_no_prompt_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    fake_tracker.install(monkeypatch, lambda *_a: pytest.fail("must not reach the tracker"))
    assert run_record.record_marker(tmp_path, "basicly-x", _entry()) is None


def test_record_marker_tolerates_a_store_that_cannot_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def refuses(*_a: object) -> None:
        raise RuntimeError("the ledger refused the write")

    fake_tracker.install(monkeypatch, refuses)
    entry = _entry(prompt_sha256="cafe", phase="build")
    assert run_record.record_marker(tmp_path, "basicly-x", entry) is None


def _export(repo_root: Path, *records: dict) -> None:
    flipped_tracker.seed_records(repo_root, records)


def test_the_cache_split_survives_serialisation_to_disk(tmp_path: Path) -> None:

    run_record.record(tmp_path, "b-1", _entry(tokens=21_610, cache_read_tokens=15_496))

    on_disk = json.loads((tmp_path / ".basicly" / "usage" / "run-records.json").read_text())
    assert on_disk["b-1"][0]["cache_read_tokens"] == 15_496
    stored = run_record.latest_record(tmp_path, "b-1")
    assert stored is not None and stored.cache_read_tokens == 15_496


def test_spend_sample_reads_a_pre_flag_entry_as_measured() -> None:
    assert run_record.spend_sample({"tokens": 90}) == (90, run_record.MEASURED)
    assert run_record.spend_sample({"tokens": 90, "estimated": True}) == (90, run_record.UNMETERED)
    assert run_record.spend_sample({"tokens": True}) is None
    assert run_record.spend_sample({"cost": 1.0}) is None


def _dispatch_payload(repo: Path, bead_id: str, entry: RunRecord) -> dict:
    run_record.record_dispatch_event(repo, bead_id, entry)
    events = [e for e in flipped_tracker.ledger_events(repo) if e.kind == "dispatch"]
    return dict(events[-1].payload)


def test_a_recorded_dispatch_carries_only_measured_tokens_as_spend(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    for bead in ("b-1", "b-2", "b-3"):
        flipped_tracker.seed(repo, bead)

    measured = _dispatch_payload(repo, "b-1", _entry(tokens=1234, estimated=False, phase="build"))
    floored = _dispatch_payload(repo, "b-2", _entry(tokens=900, estimated=True, phase="build"))
    handoff = _dispatch_payload(
        repo,
        "b-3",
        run_record.build_record(
            agent="claude", handoff=True, returncode=None, duration_s=None, command=()
        ),
    )

    assert measured["spend_micros"] == 1234
    assert (floored["spend_micros"], floored["tokens"], floored["estimated"]) == (0, 900, True)
    assert (handoff["spend_micros"], handoff["outcome"]) == (0, HANDOFF)
    assert "tokens" not in handoff


def test_two_dispatches_of_one_lane_are_told_apart_by_their_reading(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    flipped_tracker.seed(repo, "b-1")
    entry = _entry(tokens=10, phase="build")

    first = _dispatch_payload(repo, "b-1", replace(entry, timestamp="2026-08-07T00:00:00+00:00"))
    second = _dispatch_payload(repo, "b-1", replace(entry, timestamp="2026-08-07T00:05:00+00:00"))

    assert first["at"] != second["at"]
    assert {key: value for key, value in first.items() if key != "at"} == {
        key: value for key, value in second.items() if key != "at"
    }
    kit = tracker.kit(repo)
    assert kit.events.fold(flipped_tracker.ledger_events(repo)).records["b-1"].totals == (
        kit.events.Totals(events=3, attempts=2, spend_micros=20, status="open")
    )


def test_dispatch_label_falls_back_to_the_agent_when_no_model_was_pinned() -> None:
    entry = {"agent": "copilot", "model": None}
    assert run_record.dispatch_label("b-1", entry) == "b-1 on copilot"
    assert run_record.dispatch_label("b-1", {"agent": "claude", "model": "claude-opus-5"}) == (
        "b-1 on claude-opus-5"
    )
    assert run_record.dispatch_label("b-1", {}) == "b-1"

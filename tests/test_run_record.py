"""Tests for the runner run-record (basicly-z6dh).

The record is the keystone correlation artifact: per dispatched run it captures
duration + exit outcome + agent + model + token telemetry, keyed by bead id,
written atomically into the self-ignored ``.basicly/usage/``. These tests pin
that shape and — crucially — that only metadata is persisted (no prompt body).
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import run_record
from basicly.run_record import (
    EXECUTED,
    FAILED,
    HANDOFF,
    REDACTED_PROMPT,
    RUN_RECORDS_FILE,
    RunRecord,
)


def _records(repo_root: Path) -> dict:
    return json.loads((repo_root / RUN_RECORDS_FILE).read_text(encoding="utf-8"))


# --- outcome_of -------------------------------------------------------------


def test_outcome_of_labels_handoff_executed_and_failed() -> None:
    """A handoff is HANDOFF; a real run is EXECUTED only on a zero exit."""
    assert run_record.outcome_of(handoff=True, returncode=None) == HANDOFF
    assert run_record.outcome_of(handoff=False, returncode=0) == EXECUTED
    assert run_record.outcome_of(handoff=False, returncode=2) == FAILED
    assert run_record.outcome_of(handoff=False, returncode=None) == FAILED


# --- build_record -----------------------------------------------------------


def test_build_record_derives_outcome_stamps_time_and_defaults_fields() -> None:
    """build_record fills outcome + timestamp; model and token telemetry default null."""
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
    assert entry.timestamp  # ISO stamp present
    assert entry.model is None and entry.tokens is None and entry.cost is None
    assert entry.estimated is None


def test_build_record_stamps_model_provenance() -> None:
    """A pinned model is recorded as provenance (basicly-45ld); token/cost stay null."""
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
    """Token telemetry (basicly-kjc5.1) persists and round-trips per adapter shape."""
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


# --- record (write) ---------------------------------------------------------


def test_record_writes_keyed_list_and_self_ignores(tmp_path: Path) -> None:
    """A record lands under its bead id, and the usage dir self-ignores."""
    entry = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.1, command=("claude",)
    )
    run_record.record(tmp_path, "basicly-z6dh.1", entry)

    data = _records(tmp_path)
    assert list(data) == ["basicly-z6dh.1"]
    assert data["basicly-z6dh.1"][0]["outcome"] == EXECUTED
    # The usage dir self-ignores exactly like the tool-usage telemetry.
    assert (tmp_path / ".basicly/usage/.gitignore").read_text(encoding="utf-8") == "*\n"


def test_record_accumulates_history_per_bead(tmp_path: Path) -> None:
    """Re-dispatching the same bead appends, keeping the run history in order."""
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
    """A record only ever carries the redacted command, never the prompt body."""
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
    assert "implement the work" not in blob  # no dispatch-prompt text leaked


def test_record_restarts_on_a_corrupt_file(tmp_path: Path) -> None:
    """A corrupt records file restarts empty rather than failing the write."""
    (tmp_path / ".basicly/usage").mkdir(parents=True)
    (tmp_path / RUN_RECORDS_FILE).write_text("{ not json", encoding="utf-8")
    entry = run_record.build_record(
        agent="codex", handoff=True, returncode=None, duration_s=None, command=()
    )
    run_record.record(tmp_path, "i", entry)
    assert _records(tmp_path)["i"][0]["outcome"] == HANDOFF


def test_record_restarts_on_a_wrong_shaped_value(tmp_path: Path) -> None:
    """A valid-JSON but wrong-shaped bead value restarts that bead, never raising."""
    (tmp_path / ".basicly/usage").mkdir(parents=True)
    # Externally tampered: the value is a string, not the expected list.
    (tmp_path / RUN_RECORDS_FILE).write_text('{"i": "tampered"}', encoding="utf-8")
    entry = run_record.build_record(
        agent="claude", handoff=False, returncode=0, duration_s=0.1, command=("claude",)
    )
    run_record.record(tmp_path, "i", entry)  # must not raise AttributeError
    assert _records(tmp_path)["i"][0]["outcome"] == EXECUTED


# --- load_run_records (read) ------------------------------------------------


def test_load_run_records_none_when_absent(tmp_path: Path) -> None:
    """No file yet reads back as None (the hook/loop has not run)."""
    assert run_record.load_run_records(tmp_path) is None


def test_load_run_records_round_trips(tmp_path: Path) -> None:
    """A written record reads back with its fields intact (command as a list)."""
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


# --- latest_record (attribution source, basicly-140a) -----------------------


def test_latest_record_none_when_absent(tmp_path: Path) -> None:
    """No file, or no history for the bead, reads back as None."""
    assert run_record.latest_record(tmp_path, "i") is None


def test_latest_record_returns_the_most_recent_with_model(tmp_path: Path) -> None:
    """The last-appended record for the bead comes back rebuilt, model included."""
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
    """An on-disk record with a field this version does not know still loads."""
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
    data["i"][0]["future_field"] = "xyz"  # a newer writer added a field
    records_file.write_text(json.dumps(data), encoding="utf-8")
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None and latest.agent == "claude"


# --- shared evidence marker (D11, basicly-kjc5.28) ---------------------------


def test_marker_id_is_content_derived_and_attempt_aware() -> None:
    """Same inputs give the same id; a later attempt gets a distinct one."""
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
    """The marker records the inputs that make a dispatch reproducible (D9)."""
    calls: list[list[str]] = []

    def _try_run_br(_repo, args):
        calls.append(args)
        if args[:2] == ["comments", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_record.br, "try_run_br", _try_run_br)
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
    # The prompt itself is never persisted — only its digest.
    assert "prompt" not in body


def test_record_marker_is_idempotent_but_counts_a_real_rerun(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A re-record of the same dispatch takes the next attempt id, never a duplicate."""
    recorded: list[str] = []

    def _try_run_br(_repo, args):
        if args[:2] == ["comments", "list"]:
            texts = [{"text": t} for t in recorded]
            return SimpleNamespace(returncode=0, stdout=json.dumps(texts))
        if args[:2] == ["comments", "add"]:
            recorded.append(args[3])
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_record.br, "try_run_br", _try_run_br)
    entry = _entry(prompt_sha256="cafe", phase="build")
    first = run_record.record_marker(tmp_path, "basicly-x", entry)
    second = run_record.record_marker(tmp_path, "basicly-x", entry)

    assert first != second, "a second run must not collapse into the first"
    assert second is not None and second.endswith("-2")
    assert len(recorded) == 2


def test_record_marker_skips_when_there_is_no_prompt_digest(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Without a digest there is nothing to key on, so nothing is written."""
    monkeypatch.setattr(run_record.br, "try_run_br", lambda *_a: pytest.fail("must not call br"))
    assert run_record.record_marker(tmp_path, "basicly-x", _entry()) is None


def test_record_marker_tolerates_br_being_unavailable(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence is best-effort: no br means no marker, never an exception."""
    monkeypatch.setattr(run_record.br, "try_run_br", lambda *_a: None)
    entry = _entry(prompt_sha256="cafe", phase="build")
    assert run_record.record_marker(tmp_path, "basicly-x", entry) is None


# --- Ship-time cost rollup (basicly-kjc5.50) ---------------------------------


def _export(repo_root: Path, *records: dict) -> None:
    """Write a committed tracker export — the artifact a fresh clone has."""
    beads = repo_root / ".beads"
    beads.mkdir(parents=True, exist_ok=True)
    lines = [json.dumps(record) for record in records]
    (beads / "issues.jsonl").write_text("\n".join(lines) + "\n", encoding="utf-8")


def _run_comment(**payload) -> dict:
    ident = payload.pop("id", "basicly-x#run-1")
    return {"text": f"{run_record.MARKER} id={ident} phase=build\n{json.dumps(payload)}"}


def test_cost_rollup_sums_every_dispatch_including_the_failed_ones() -> None:
    """The actual is the whole package: a cheap final attempt never hides an expensive one."""
    history = [
        {"outcome": FAILED, "tokens": 30_000, "cost": 0.60, "duration_s": 400.0},
        {"outcome": EXECUTED, "tokens": 12_000, "cost": 0.24, "duration_s": 100.5},
        {"outcome": HANDOFF},  # nothing executed: counted as a dispatch, meters nothing
    ]
    rolled = run_record.cost_rollup(history, rework=1)
    assert rolled.dispatches == 3
    assert rolled.tokens == 42_000
    assert rolled.cost == pytest.approx(0.84)
    assert rolled.wall_clock_s == pytest.approx(500.5)
    assert rolled.rework == 1
    assert rolled.estimated is False


def test_cost_rollup_reports_none_for_what_was_never_metered() -> None:
    """An unmetered package reads as null, never as a measured zero."""
    rolled = run_record.cost_rollup([{"outcome": HANDOFF}, {"outcome": HANDOFF}])
    assert rolled.dispatches == 2
    assert rolled.tokens is None and rolled.cost is None and rolled.wall_clock_s is None
    assert rolled.rework is None  # unreadable rework markers stay unclaimed


def test_cost_rollup_flags_an_estimated_sample() -> None:
    """A chars/4 transcript estimate in the sum is declared, so a consumer can down-weight it."""
    history = [{"tokens": 10, "estimated": False}, {"tokens": 90, "estimated": True}]
    assert run_record.cost_rollup(history).estimated is True


def test_record_cost_marker_writes_the_forecast_beside_the_actual(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One marker carries the pair, the counts, and the class the sample belongs to."""
    calls: list[list[str]] = []

    def _try_run_br(_repo, args):
        calls.append(args)
        if args[:2] == ["comments", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_record.br, "try_run_br", _try_run_br)
    ident = run_record.record_cost_marker(
        tmp_path,
        "basicly-x",
        actual=run_record.cost_rollup(
            [{"tokens": 12_000, "cost": 0.3, "duration_s": 60.0}], rework=2
        ),
        forecast=run_record.CostForecast(tokens=24_000),
        task_class="task",
        scope_tokens=8_000,
    )

    assert ident == "basicly-x#cost"
    add = next(c for c in calls if c[:2] == ["comments", "add"])
    header, payload = add[3].split("\n", 1)
    assert header == f"{run_record.COST_MARKER} id=basicly-x#cost"
    body = json.loads(payload)
    assert body["task_class"] == "task" and body["scope_tokens"] == 8_000
    assert body["forecast"]["tokens"] == 24_000
    assert body["actual"] == {
        "dispatches": 1,
        "tokens": 12_000,
        "cost": 0.3,
        "wall_clock_s": 60.0,
        "rework": 2,
        "estimated": False,
    }
    # Money is never forecast, and wall-clock waits on the duration predictor
    # (basicly-kjc5.48) — both keys are present and null, not absent.
    assert body["forecast"]["cost"] is None and body["forecast"]["wall_clock_s"] is None


def test_record_cost_marker_never_writes_a_second_rollup(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """One package, one ledger entry: a re-record would double-count it."""
    recorded: list[str] = []

    def _try_run_br(_repo, args):
        if args[:2] == ["comments", "list"]:
            return SimpleNamespace(returncode=0, stdout=json.dumps([{"text": t} for t in recorded]))
        if args[:2] == ["comments", "add"]:
            recorded.append(args[3])
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_record.br, "try_run_br", _try_run_br)
    rollup = run_record.cost_rollup([{"tokens": 1}])
    forecast = run_record.CostForecast()
    first = run_record.record_cost_marker(tmp_path, "b-1", actual=rollup, forecast=forecast)
    second = run_record.record_cost_marker(tmp_path, "b-1", actual=rollup, forecast=forecast)

    assert first == "b-1#cost" and second is None
    assert len(recorded) == 1


def test_record_cost_marker_reports_nothing_written_when_br_refuses(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A failed write is not a recorded rollup — the caller must not claim one."""
    monkeypatch.setattr(
        run_record.br,
        "try_run_br",
        lambda *_a: SimpleNamespace(returncode=2, stdout=""),
    )
    written = run_record.record_cost_marker(
        tmp_path,
        "b-1",
        actual=run_record.cost_rollup([]),
        forecast=run_record.CostForecast(),
    )
    assert written is None


def test_tracker_history_reads_the_dispatches_a_clone_would_see(tmp_path: Path) -> None:
    """The export carries comments, so the ledger travels where .basicly/usage cannot."""
    _export(
        tmp_path,
        {
            "id": "b-1",
            "comments": [
                _run_comment(id="b-1#run-a", tokens=100, timestamp="2026-07-26T10:00:00+00:00"),
                {"text": "[harness-info] not a dispatch"},
            ],
        },
        {"id": "b-2", "comments": []},
    )
    history = run_record.tracker_history(tmp_path)
    assert list(history) == ["b-1"]
    assert history["b-1"][0]["tokens"] == 100


def test_dispatch_history_unions_local_records_with_the_tracker_once(tmp_path: Path) -> None:
    """A dispatch recorded in both places is one sample, not two."""
    entry = _entry(tokens=100, prompt_sha256="cafe", phase="build")
    run_record.record(tmp_path, "b-1", entry)
    _export(
        tmp_path,
        {
            "id": "b-1",
            "comments": [_run_comment(tokens=100, timestamp=entry.timestamp)],
        },
        {
            "id": "b-2",  # only the tracker knows this one: another machine ran it
            "comments": [_run_comment(tokens=7, timestamp="2026-07-26T09:00:00+00:00")],
        },
    )
    history = run_record.dispatch_history(tmp_path)
    assert len(history["b-1"]) == 1
    assert len(history["b-2"]) == 1


def _cost_comment(bead: str, **actual) -> dict:
    payload = {"bead": bead, "forecast": {"tokens": None}, "actual": actual}
    return {"text": f"{run_record.COST_MARKER} id={bead}#cost\n{json.dumps(payload)}"}


def test_landed_package_cost_is_computable_from_the_tracker_alone(tmp_path: Path) -> None:
    """Cost per landed package needs the export only — no local usage file (basicly-7bur)."""
    _export(
        tmp_path,
        {
            "id": "b-1",
            "comments": [_cost_comment("b-1", dispatches=2, tokens=30_000, cost=0.6)],
        },
        {
            "id": "b-2",
            "comments": [_cost_comment("b-2", dispatches=1, tokens=10_000, cost=0.2)],
        },
        {"id": "b-3", "comments": [{"text": "[harness-info] never shipped"}]},
    )
    assert not (tmp_path / run_record.USAGE_DIR).exists()

    landed = run_record.landed_package_cost(tmp_path)
    assert landed.packages == 2
    assert landed.tokens == 40_000
    assert landed.cost == pytest.approx(0.8)
    assert landed.per_package("cost") == pytest.approx(0.4)
    assert landed.per_package("tokens") == pytest.approx(20_000)
    assert landed.per_package("wall_clock_s") is None  # never metered, never invented


def test_landed_package_cost_without_an_export(tmp_path: Path) -> None:
    """No tracker export means no landed packages, not a crash."""
    landed = run_record.landed_package_cost(tmp_path)
    assert landed.packages == 0 and landed.per_package("cost") is None

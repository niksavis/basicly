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


def test_build_record_carries_the_token_split_and_credits(tmp_path: Path) -> None:
    """The per-kind split and AI credits persist beside the summed total (basicly-2rn9).

    `tokens` stays the single summed total every consumer reads (the D3 grant
    ceiling, sizing calibration, the cost rollups), so the split is a sibling and
    not a redefinition: it is deliberately *not* the sum of the split fields,
    because copilot's inputTokens already contains both cache counts.
    """
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
    # A zero is a measurement, so it must survive serialization, not be dropped.
    assert (stored["cache_read_tokens"], stored["cache_write_tokens"]) == (0, 24208)
    assert stored["reasoning_tokens"] == 0
    # Credits are AI credits, never USD: `cost` stays null for a copilot dispatch.
    assert stored["credits"] == 6.0564
    assert stored["cost"] is None
    latest = run_record.latest_record(tmp_path, "i")
    assert latest is not None
    assert (latest.credits, latest.cache_write_tokens) == (6.0564, 24208)


def test_build_record_leaves_the_split_null_for_a_splitless_adapter(tmp_path: Path) -> None:
    """An adapter reporting only a total records nulls, not zeros — absent is not zero."""
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


def test_record_marker_carries_the_dispatch_ordering(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Ordering must travel in the marker, not only on disk (basicly-vkh0.3).

    The AC asks for the pass ordering to be reconstructible *from the tracker
    alone*, and ``.basicly/usage/`` never leaves the machine — so the marker, which
    br exports in ``issues.jsonl``, is the half that has to carry it.
    """

    def _try_run_br(_repo, args):
        if args[:2] == ["comments", "list"]:
            return SimpleNamespace(returncode=0, stdout="[]")
        _try_run_br.added = args  # type: ignore[attr-defined]
        return SimpleNamespace(returncode=0, stdout="")

    monkeypatch.setattr(run_record.br, "try_run_br", _try_run_br)
    entry = _entry(
        prompt_sha256="deadbeef",
        phase="lane",
        dispatch_rank=2,
        scheduler_rank=1,
        scheduler_fallback_rank=3,
        scheduler_score=45,
        scheduler_policy="br.scheduler.v1",
    )
    run_record.record_marker(tmp_path, "basicly-x", entry)

    body = json.loads(_try_run_br.added[3].split("\n", 1)[1])  # type: ignore[attr-defined]
    assert body["dispatch_rank"] == 2
    assert body["scheduler_rank"] == 1
    assert body["scheduler_fallback_rank"] == 3
    assert body["scheduler_score"] == 45
    assert body["scheduler_policy"] == "br.scheduler.v1"


def test_build_record_defaults_the_ordering_to_unrecorded(tmp_path: Path) -> None:
    """A dispatch outside a supervisor pass has no ranking, and must say so with nulls."""
    _ = tmp_path
    entry = run_record.build_record(
        agent="codex", handoff=False, returncode=0, duration_s=1.0, command=("codex",)
    )
    assert entry.dispatch_rank is None
    assert entry.scheduler_policy is None


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


# --- forecast error, per dispatch record (basicly-jr0l.34) -------------------


def test_a_dispatch_record_carries_the_forecast_beside_the_actual(tmp_path: Path) -> None:
    """The join this bead exists for: one record holds both halves and the class.

    Before this, `forecast_tokens` was a declared field with no writer, so the two
    numbers lived on disjoint records and no forecast error was ever computable.
    """
    entry = _entry(
        tokens=9_430_203,
        forecast_tokens=57_965,
        scope_tokens=19_000,
        task_class="task",
        forecast_source="dispatch",
    )
    run_record.record(tmp_path, "b-1", entry)
    stored = _records(tmp_path)["b-1"][0]
    assert stored["forecast_tokens"] == 57_965
    assert stored["tokens"] == 9_430_203
    assert stored["task_class"] == "task"
    assert stored["forecast_source"] == "dispatch"


def test_forecast_errors_pairs_a_complete_record(tmp_path: Path) -> None:
    """A record with both halves yields a ratio and a signed miss."""
    run_record.record(
        tmp_path,
        "b-1",
        _entry(tokens=200_000, forecast_tokens=50_000, task_class="task", model="opus"),
    )
    report = run_record.forecast_errors(tmp_path)
    assert report.paired == 1
    error = report.errors[0]
    assert error.bead == "b-1"
    assert error.ratio == pytest.approx(4.0)
    assert error.error_tokens == 150_000
    assert error.task_class == "task" and error.model == "opus"


def test_forecast_errors_refuses_a_record_missing_either_half(tmp_path: Path) -> None:
    """Counting a missing half as zero would fabricate an error; it is reported unpaired.

    The three shapes are real: a forecast with no actual is a handoff or a killed
    run, an actual with no forecast is an un-sized helper dispatch (the rubric judge,
    the decider), and neither is a handoff that was never sized.
    """
    run_record.record(tmp_path, "b-1", _entry(forecast_tokens=50_000))
    run_record.record(tmp_path, "b-2", _entry(tokens=200_000))
    run_record.record(tmp_path, "b-3", _entry())
    report = run_record.forecast_errors(tmp_path)
    assert report.paired == 0 and report.errors == ()
    assert (report.forecast_only, report.actual_only, report.unmetered) == (1, 1, 1)


def test_forecast_errors_refuses_a_zero_forecast_rather_than_dividing_by_it(
    tmp_path: Path,
) -> None:
    """A zero forecast is a recording defect, not a prediction, and cannot be a divisor."""
    run_record.record(tmp_path, "b-1", _entry(tokens=200_000, forecast_tokens=0))
    report = run_record.forecast_errors(tmp_path)
    assert report.paired == 0 and report.actual_only == 1


def test_forecast_errors_reports_the_median_ratio_not_the_mean(tmp_path: Path) -> None:
    """One 400x sample must not drag the summary somewhere no dispatch has been."""
    for bead, tokens in (("b-1", 100_000), ("b-2", 200_000), ("b-3", 40_000_000)):
        run_record.record(tmp_path, bead, _entry(tokens=tokens, forecast_tokens=100_000))
    report = run_record.forecast_errors(tmp_path)
    assert report.median_ratio == pytest.approx(2.0)


def test_forecast_errors_has_no_median_without_a_pair(tmp_path: Path) -> None:
    """None, never zero: nothing measured must not read as a perfect forecast."""
    assert run_record.forecast_errors(tmp_path).median_ratio is None


def test_forecast_errors_groups_by_task_class_and_drops_the_unclassed(
    tmp_path: Path,
) -> None:
    """Calibration is per class, and a sample with no class recorded cannot join one."""
    run_record.record(
        tmp_path, "b-1", _entry(tokens=200_000, forecast_tokens=100_000, task_class="task")
    )
    run_record.record(
        tmp_path, "b-2", _entry(tokens=300_000, forecast_tokens=100_000, task_class="bug")
    )
    run_record.record(tmp_path, "b-3", _entry(tokens=400_000, forecast_tokens=100_000))
    grouped = run_record.forecast_errors(tmp_path).by_task_class()
    assert sorted(grouped) == ["bug", "task"]
    assert len(grouped["task"]) == 1


def test_forecast_errors_sees_a_dispatch_only_the_tracker_carries(tmp_path: Path) -> None:
    """A teammate's dispatch pairs too: the export travels where .basicly/usage does not."""
    _export(
        tmp_path,
        {
            "id": "b-9",
            "comments": [
                _run_comment(
                    tokens=250_000,
                    forecast_tokens=50_000,
                    task_class="chore",
                    timestamp="2026-07-26T09:00:00+00:00",
                )
            ],
        },
    )
    report = run_record.forecast_errors(tmp_path)
    assert report.paired == 1 and report.errors[0].bead == "b-9"
    assert report.errors[0].ratio == pytest.approx(5.0)


def test_forecast_errors_flags_an_estimated_actual(tmp_path: Path) -> None:
    """A chars/4 actual is a weaker sample and must be identifiable as one (design 7.5)."""
    run_record.record(
        tmp_path, "b-1", _entry(tokens=200_000, forecast_tokens=100_000, estimated=True)
    )
    assert run_record.forecast_errors(tmp_path).errors[0].estimated is True


# --- spend forecast calibration, per model (basicly-jr0l.21) -----------------


def _pair(**overrides) -> run_record.ForecastError:
    """One paired record: 200x the forecast, at 0.80 USD and 100 s per million tokens."""
    fields = {
        "bead": "b-1",
        "timestamp": "2026-07-26T09:00:00+00:00",
        "forecast_tokens": 50_000,
        "actual_tokens": 10_000_000,
        "task_class": "task",
        "model": "claude-opus-5",
        "actual_cost": 8.0,
        "actual_wall_clock_s": 1_000.0,
        # A calibration sample must be a write dispatch (basicly-tcmy.5); the helper
        # builds the eligible shape so a test about a *ratio* is not silently a test
        # about the phase filter.
        "phase": run_record.LANE_PHASE,
    }
    fields.update(overrides)
    return run_record.ForecastError(**fields)


def _report(*pairs: run_record.ForecastError) -> run_record.ForecastErrorReport:
    return run_record.ForecastErrorReport(errors=pairs)


def _calibrate(report: run_record.ForecastErrorReport, **overrides):
    kwargs = {
        "model": "claude-opus-5",
        "task_class": "task",
        "min_samples": 3,
        "window": 50,
    }
    kwargs.update(overrides)
    return run_record.calibrate_spend(report, **kwargs)


def test_a_pair_carries_the_money_and_time_the_dispatch_spent(tmp_path: Path) -> None:
    """The cost and duration ride on the pair, so a price is keyed to a model and class."""
    run_record.record(
        tmp_path,
        "b-1",
        _entry(
            tokens=10_000_000,
            forecast_tokens=50_000,
            task_class="task",
            model="claude-opus-5",
            cost=8.0,
        ),
    )
    error = run_record.forecast_errors(tmp_path).errors[0]
    assert error.actual_cost == pytest.approx(8.0)
    # _entry's dispatch ran 1.5s; the pair carries what was metered, not a re-derivation.
    assert error.actual_wall_clock_s == pytest.approx(1.5)


def test_a_pair_carries_the_phase_and_the_factors_provenance(tmp_path: Path) -> None:
    """The two fields basicly-tcmy.5 adds travel from the record onto the pair.

    The phase is what lets a calibration refuse a helper's sample, and the build-factor
    source is what stops the forecast half of the pair from reading as a measurement.
    Both are read off the persisted record rather than re-derived, because by the time a
    calibration runs the config that produced the factor may have moved on.
    """
    run_record.record(
        tmp_path,
        "b-1",
        _entry(
            tokens=10_000_000,
            forecast_tokens=50_000,
            task_class="task",
            model="claude-opus-5",
            phase=run_record.LANE_PHASE,
            build_factor_source="seed",
        ),
    )

    stored = run_record.latest_record(tmp_path, "b-1")
    assert stored is not None
    assert stored.build_factor_source == "seed"
    assert run_record.forecast_errors(tmp_path).errors[0].phase == run_record.LANE_PHASE


def test_calibrate_spend_seeds_every_ratio_from_the_declared_prior() -> None:
    """With no history the declared prior stands, and says so on every ratio."""
    calibration = _calibrate(_report())
    prior = run_record.DECLARED_SPEND_PRIOR
    assert calibration.tokens_per_working_set_token.value == prior.tokens_per_working_set_token
    assert calibration.usd_per_million_tokens.value == prior.usd_per_million_tokens
    assert calibration.seconds_per_million_tokens.value == prior.seconds_per_million_tokens
    assert calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO
    assert calibration.measured is False
    # The prior travels with the forecast: a seeded number that reads as measured is
    # worse than no number.
    assert calibration.prior is prior
    assert calibration.pairs == 0


def test_calibrate_spend_replaces_the_prior_past_the_minimum() -> None:
    """Past min_samples the measured median per (model, class) replaces every ratio."""
    pairs = (
        _pair(actual_tokens=5_000_000, actual_cost=5.0, actual_wall_clock_s=250.0),
        _pair(actual_tokens=10_000_000, actual_cost=8.0, actual_wall_clock_s=1_000.0),
        _pair(actual_tokens=20_000_000, actual_cost=24.0, actual_wall_clock_s=4_000.0),
    )
    calibration = _calibrate(_report(*pairs))
    assert calibration.measured is True
    # Ratios 100x / 200x / 400x -> median 200x, not the 233x mean.
    assert calibration.tokens_per_working_set_token.value == pytest.approx(200.0)
    assert calibration.tokens_per_working_set_token.source == run_record.MEASURED_RATIO
    assert calibration.tokens_per_working_set_token.samples == 3
    # 1.00 / 0.80 / 1.20 USD per million -> median 1.00.
    assert calibration.usd_per_million_tokens.value == pytest.approx(1.0)
    # 50 / 100 / 200 seconds per million -> median 100.
    assert calibration.seconds_per_million_tokens.value == pytest.approx(100.0)
    assert calibration.pairs == 3


def test_calibrate_spend_holds_the_prior_below_the_minimum() -> None:
    """Two pairs under a minimum of three leave the seed in place, counted honestly."""
    calibration = _calibrate(_report(_pair(), _pair()))
    assert calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO
    assert calibration.tokens_per_working_set_token.samples == 2
    assert calibration.pairs == 2


def test_calibrate_spend_never_borrows_another_models_history() -> None:
    """The same work costs different amounts per model, so a foreign sample cannot key in."""
    pairs = tuple(_pair(model="some-other-model") for _ in range(5))
    calibration = _calibrate(_report(*pairs))
    assert calibration.pairs == 0
    assert calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO


def test_calibrate_spend_refuses_the_records_with_no_model_recorded() -> None:
    """An unrecorded model is unknown provenance, not a match for an unresolved one.

    All 122 historical records carried a null model (basicly-kjc5.59), and pooling
    those would rebuild exactly the cross-model average this keying exists to avoid.
    """
    pairs = tuple(_pair(model=None) for _ in range(5))
    assert _calibrate(_report(*pairs), model=None).pairs == 0
    assert _calibrate(_report(*pairs)).pairs == 0


def test_calibrate_spend_never_borrows_another_task_class() -> None:
    """A chore's spend does not predict a task's, so the class is part of the key."""
    pairs = tuple(_pair(task_class="chore") for _ in range(5))
    assert _calibrate(_report(*pairs)).pairs == 0


def test_calibrate_spend_excludes_a_chars_over_four_estimate() -> None:
    """A chars/4 actual is too weak to calibrate money with (design 7.5)."""
    pairs = tuple(_pair(estimated=True) for _ in range(5))
    assert _calibrate(_report(*pairs)).pairs == 0


@pytest.mark.parametrize(
    "phase",
    [run_record.VALIDATE_PHASE, run_record.DECIDE_PHASE, None, "", "probe"],
)
def test_calibrate_spend_refuses_a_dispatch_that_is_not_a_lane(phase: str | None) -> None:
    """AC: a rubric judge and the decider are not samples of what the work costs.

    They are dispatched on the same bead and land in the same record stream, so a
    filter on model and class alone admits them (basicly-tcmy.5) — and the ratio would
    then be a helper's spend over a lane's working set, dragging the multiplier the
    band and the budget are both computed from. A record whose phase was never written
    is refused on the same rule: unknown provenance fails closed.
    """
    pairs = tuple(_pair(phase=phase) for _ in range(5))
    calibration = _calibrate(_report(*pairs))
    assert calibration.pairs == 0
    assert calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO


def test_the_calibration_and_the_lane_bound_read_one_phase_set() -> None:
    """The two consumers that disagreed now share one definition of a write dispatch.

    Both filters are ``is_write_phase``, and this pins the set they resolve against:
    the interactive build and the supervised lane are in it, the helpers are not. The
    defect was not a wrong value in either filter but that there were two of them.
    """
    assert set(run_record.WRITE_PHASES) == {run_record.BUILD_PHASE, run_record.LANE_PHASE}
    assert run_record.is_write_phase(run_record.BUILD_PHASE)
    assert run_record.is_write_phase(run_record.LANE_PHASE)
    assert not run_record.is_write_phase(run_record.VALIDATE_PHASE)
    assert not run_record.is_write_phase(run_record.DECIDE_PHASE)
    # A record read off disk can carry anything, including nothing.
    assert not run_record.is_write_phase(None)
    assert not run_record.is_write_phase(1)


def test_calibrate_spend_keeps_only_the_newest_window() -> None:
    """The window is a rolling one: an old sample must not outvote the recent ones."""
    old = tuple(
        _pair(timestamp=f"2026-07-0{day}T09:00:00+00:00", actual_tokens=50_000_000)
        for day in (1, 2, 3)
    )
    recent = tuple(
        _pair(timestamp=f"2026-07-2{day}T09:00:00+00:00", actual_tokens=10_000_000)
        for day in (4, 5, 6)
    )
    calibration = _calibrate(_report(*old, *recent), window=3)
    assert calibration.pairs == 3
    assert calibration.tokens_per_working_set_token.value == pytest.approx(200.0)


def test_calibrate_spend_measures_tokens_while_money_stays_seeded() -> None:
    """History accumulates unevenly, so each ratio names its own source.

    An adapter that bills in credits reports no USD at all (basicly-2rn9), so a
    forecast is routinely measured in tokens and still seeded in money.
    """
    pairs = tuple(_pair(actual_cost=None) for _ in range(3))
    calibration = _calibrate(_report(*pairs))
    assert calibration.tokens_per_working_set_token.source == run_record.MEASURED_RATIO
    assert calibration.usd_per_million_tokens.source == run_record.PRIOR_RATIO
    assert calibration.usd_per_million_tokens.samples == 0
    assert calibration.measured is True


def test_calibrate_spend_reports_no_number_for_an_undeclared_ratio() -> None:
    """No prior and no history is indeterminate — it must not publish a confident zero."""
    undeclared = run_record.SpendPrior(
        tokens_per_working_set_token=None,
        usd_per_million_tokens=None,
        seconds_per_million_tokens=None,
        basis="nothing declared",
    )
    calibration = _calibrate(_report(), prior=undeclared)
    assert calibration.tokens_per_working_set_token.value is None
    assert calibration.tokens_per_working_set_token.source == run_record.UNDECLARED_RATIO
    assert calibration.measured is False


def test_the_declared_prior_matches_the_measured_proof_run() -> None:
    """The seed is derived, not invented: the medians of the three metered packages.

    Pinned so the numbers cannot be quietly edited away from the evidence in
    basicly-jr0l.21 — the lanes are kjc5.32/.50/.51 and the medians are the middle
    lane's token multiplier, USD per million and seconds per million.
    """
    prior = run_record.DECLARED_SPEND_PRIOR
    assert prior.tokens_per_working_set_token == pytest.approx(16_002_352 / 47_847, rel=1e-3)
    assert prior.usd_per_million_tokens == pytest.approx(11.729733 / 16.002352, rel=1e-3)
    assert prior.seconds_per_million_tokens == pytest.approx(1_474.7 / 16.002352, rel=1e-3)
    assert "basicly-u6jq.1" in prior.basis


def test_calibrate_spend_never_medians_an_empty_sample_set() -> None:
    """A minimum of zero must not make "no samples" satisfy the minimum and raise."""
    calibration = _calibrate(_report(), min_samples=0)
    assert calibration.tokens_per_working_set_token.source == run_record.PRIOR_RATIO
    assert calibration.pairs == 0

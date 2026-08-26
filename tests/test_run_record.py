"""Tests for the runner run-record (basicly-z6dh).

The record is the keystone correlation artifact: per dispatched run it captures
duration + exit outcome + agent + model + token telemetry, keyed by bead id,
written atomically into the self-ignored ``.basicly/usage/``. These tests pin
that shape and — crucially — that only metadata is persisted (no prompt body).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace

import pytest

from basicly import context_meter, run_record, runner
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


# --- The context ceiling stays explicable from the ledger (D23) -------------


def test_a_dispatch_records_the_occupancy_and_the_window_its_ceiling_came_from(
    tmp_path: Path,
) -> None:
    """The demoted ceiling's two numbers survive the run they were measured on.

    The ceiling acts on nothing since D23, so the record is where the eighteen
    ``(context-ceiling overrun)`` beads it once spun stay explicable: the occupancy it
    compared, the window the threshold came from, and where that window was declared.
    The 200000 is the historical declaration, written down rather than read off today's
    ``BUILTIN_RUNNERS`` — the shipped figure has since moved (basicly-89hm), and a test
    that re-derives history from the current tree stops describing the beads it exists
    to explain. The threshold is still recomputed through
    :func:`context_meter.ceiling_tokens`, because the 120000 those beads fired under *is*
    that window times the configured fraction, and 145570 is what basicly-kjc5.42
    actually recorded against it.
    """
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
    """Without a digest there is nothing to key on, so nothing is written."""
    fake_tracker.install(monkeypatch, lambda *_a: pytest.fail("must not reach the tracker"))
    assert run_record.record_marker(tmp_path, "basicly-x", _entry()) is None


def test_record_marker_tolerates_a_store_that_cannot_answer(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Evidence is best-effort: an unwritable store means no marker, never an exception."""

    def refuses(*_a: object) -> None:
        raise RuntimeError("the ledger refused the write")

    fake_tracker.install(monkeypatch, refuses)
    entry = _entry(prompt_sha256="cafe", phase="build")
    assert run_record.record_marker(tmp_path, "basicly-x", entry) is None


# --- Ship-time cost rollup (basicly-kjc5.50) ---------------------------------


def _export(repo_root: Path, *records: dict) -> None:
    """Seed the committed ledger — the artifact a fresh clone has."""
    flipped_tracker.seed_records(repo_root, records)


def test_the_cache_split_survives_serialisation_to_disk(tmp_path: Path) -> None:
    """A parsed cache split must reach the file, not just the dataclass (basicly-p16y).

    `basicly-i4gg` taught the parser claude's four disjoint counts and
    ``test_runner_usage`` pins that half; nothing pinned the half after it. The
    failure this guards is a serialiser that elides the pair — it would leave every
    record cache-blind while the parser looked correct, and the only symptom is a
    field reading absent, which is also what a dispatch predating the parser looks
    like. Those two were indistinguishable on 2026-08-13: 0 of 134 records carried
    the split, and it took the merge timestamps to show why (every dispatch ran six
    hours before the parser landed).

    The raw JSON is asserted rather than only the round-trip, because
    ``spend_calibration`` and the cost rollup read the file, not the dataclass.
    """
    run_record.record(tmp_path, "b-1", _entry(tokens=21_610, cache_read_tokens=15_496))

    on_disk = json.loads((tmp_path / ".basicly" / "usage" / "run-records.json").read_text())
    assert on_disk["b-1"][0]["cache_read_tokens"] == 15_496
    stored = run_record.latest_record(tmp_path, "b-1")
    assert stored is not None and stored.cache_read_tokens == 15_496


def test_spend_sample_reads_a_pre_flag_entry_as_measured() -> None:
    """Tokens with no `estimated` field predate it and keep the meaning they were written with."""
    assert run_record.spend_sample({"tokens": 90}) == (90, run_record.MEASURED)
    assert run_record.spend_sample({"tokens": 90, "estimated": True}) == (90, run_record.UNMETERED)
    assert run_record.spend_sample({"tokens": True}) is None
    assert run_record.spend_sample({"cost": 1.0}) is None


def test_dispatch_label_falls_back_to_the_agent_when_no_model_was_pinned() -> None:
    """A halt must still say *which runner*, and a family with no model flag pins none."""
    entry = {"agent": "copilot", "model": None}
    assert run_record.dispatch_label("b-1", entry) == "b-1 on copilot"
    assert run_record.dispatch_label("b-1", {"agent": "claude", "model": "claude-opus-5"}) == (
        "b-1 on claude-opus-5"
    )
    assert run_record.dispatch_label("b-1", {}) == "b-1"

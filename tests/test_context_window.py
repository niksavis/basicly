"""Where a dispatch's context window comes from, and what it says when nothing does.

The defect these pin (basicly-89hm) is not that 200_000 was the wrong number — it was
right for one of the two models the probe below caught in a single dispatch. It is that
a figure nobody had checked was indistinguishable from one somebody chose, and it
shipped to every consumer that installed the harness without writing an override.
"""

from __future__ import annotations

import json
from datetime import UTC, date, datetime, timedelta
from pathlib import Path

import pytest

from basicly import context_window, run_record, runner
from basicly.context_window import (
    ADAPTER_WINDOW,
    ADAPTER_WINDOWS,
    DECLARED_WINDOW,
    DEFAULT_CONTEXT_WINDOW,
    FALLBACK_WINDOW,
    OBSERVED_WINDOW,
    RECHECK_DAYS,
    UNMETERED,
    AdapterWindow,
    reported_window,
    resolve,
    stale_declarations,
)
from basicly.runner import BUILTIN_RUNNERS, RunnerSpec, RunResult

# Captured from a real dispatch, not composed from documentation: claude 2.1.233 on
# 2026-08-15, `claude -p 'reply with the single word ok' --output-format stream-json
# --verbose`. Trimmed to the fields the reader keys on; every value is verbatim.
#
# It is the evidence for the whole module. **One dispatch reported two windows** — the
# fast model at 200_000 and the session's own at 1_000_000 — which is why a per-adapter
# constant cannot be right and why the pairing is done through the final turn.
PROBE = "\n".join(
    json.dumps(event)
    for event in (
        {"type": "system", "subtype": "init", "model": "claude-opus-5[1m]"},
        {
            "type": "assistant",
            "message": {
                "model": "claude-opus-5",
                "usage": {"input_tokens": 2, "cache_read_input_tokens": 16305},
            },
        },
        {
            "type": "result",
            "subtype": "success",
            "modelUsage": {
                "claude-haiku-4-5-20251001": {
                    "inputTokens": 522,
                    "contextWindow": 200_000,
                    "canonicalModel": "claude-haiku-4-5",
                },
                "claude-opus-5[1m]": {
                    "inputTokens": 2,
                    "contextWindow": 1_000_000,
                    "canonicalModel": "claude-opus-5",
                },
            },
        },
    )
)


def _stream(*events: dict) -> str:
    return "\n".join(json.dumps(event) for event in events)


def test_the_reported_window_is_the_one_the_final_turn_ran_under() -> None:
    """The captured dispatch reports 200_000 and 1_000_000; the metered turn is opus.

    `runner.context_occupancy` measures the last non-forwarded assistant turn, so the
    denominator has to be that turn's model. Taking the first key would answer 200_000
    here — the stale constant, arrived at a second way.
    """
    assert reported_window(PROBE) == 1_000_000


def test_a_window_is_unreported_rather_than_assumed() -> None:
    """Unparseable, absent and malformed all read as *no measurement*, never a default."""
    assert reported_window("") is None
    assert reported_window("not json at all\n{") is None
    assert reported_window(_stream({"type": "result", "modelUsage": {}})) is None
    assert (
        reported_window(_stream({"type": "result", "modelUsage": {"m": {"inputTokens": 1}}}))
        is None
    )


@pytest.mark.parametrize("window", [0, -1, True, "1000000", None])
def test_a_window_that_is_not_a_count_of_tokens_is_not_a_window(window: object) -> None:
    """A bool is an int in python, and a run cannot occupy a window of zero."""
    stream = _stream({"type": "result", "modelUsage": {"m": {"contextWindow": window}}})
    assert reported_window(stream) is None


def test_one_reported_window_needs_no_final_turn_to_pick_it() -> None:
    """Nothing to choose between, so a stream that never names its model still meters."""
    stream = _stream({"type": "result", "modelUsage": {"m": {"contextWindow": 400_000}}})
    assert reported_window(stream) == 400_000


def test_several_reported_windows_with_no_identifiable_turn_report_nothing() -> None:
    """The 5x spread in the capture is exactly what a guess here would get wrong."""
    stream = _stream({
        "type": "result",
        "modelUsage": {
            "a": {"contextWindow": 200_000},
            "b": {"contextWindow": 1_000_000},
        },
    })
    assert reported_window(stream) is None


def test_a_forwarded_subagent_turn_does_not_choose_the_lanes_window() -> None:
    """A subagent runs its own model, so its window is not the lane's.

    Same rule `claude_last_turn_usage` takes for the numerator: read the subagent's
    turn and the pair stops describing one run.
    """
    forwarded_last = _stream(
        {"type": "assistant", "message": {"model": "claude-opus-5"}},
        {
            "type": "assistant",
            "parent_tool_use_id": "toolu_1",
            "message": {"model": "claude-haiku-4-5"},
        },
        {
            "type": "result",
            "modelUsage": {
                "claude-haiku-4-5-20251001": {
                    "contextWindow": 200_000,
                    "canonicalModel": "claude-haiku-4-5",
                },
                "claude-opus-5[1m]": {
                    "contextWindow": 1_000_000,
                    "canonicalModel": "claude-opus-5",
                },
            },
        },
    )
    assert reported_window(forwarded_last) == 1_000_000


# --- resolution: chosen, then measured, then dated, then refused ---------------


def test_a_declaration_wins_over_the_adapters_own_report() -> None:
    """The record has to explain the threshold the engine acted on before the run."""
    assert resolve(declared=600_000, source=DECLARED_WINDOW, reported=1_000_000) == (
        600_000,
        DECLARED_WINDOW,
    )


def test_a_consumer_with_no_override_meters_the_window_the_adapter_reported() -> None:
    """AC: a consumer that declares nothing does not inherit a smaller window.

    The shipped default is a figure about a model this dispatch may not have run; the
    report is a measurement of the model it did.
    """
    assert resolve(declared=200_000, source=ADAPTER_WINDOW, reported=1_000_000) == (
        1_000_000,
        OBSERVED_WINDOW,
    )


def test_a_dated_adapter_default_carries_a_run_the_adapter_said_nothing_about() -> None:
    """A killed run reports no result event, and the default behind it was checked."""
    assert resolve(declared=1_000_000, source=ADAPTER_WINDOW, reported=None) == (
        1_000_000,
        ADAPTER_WINDOW,
    )


def test_an_undeclared_unreported_window_is_recorded_as_unmetered() -> None:
    """AC: the engine records that it could not meter rather than assuming a default.

    codex and copilot report no window at all, so this is their ordinary case — and a
    conservative 128_000 presented as the denominator would make every occupancy above
    it read as an overrun of a threshold nobody set.
    """
    assert resolve(declared=DEFAULT_CONTEXT_WINDOW, source=FALLBACK_WINDOW, reported=None) == (
        None,
        UNMETERED,
    )
    assert resolve(declared=DEFAULT_CONTEXT_WINDOW, source=None, reported=None) == (None, UNMETERED)


def test_the_unmetered_label_says_which_of_the_two_inputs_was_missing() -> None:
    """An "unmetered" with no reason is unactionable: declare a window, or read why none arrived."""
    assert "declared" in UNMETERED and "reported" in UNMETERED


# --- the shipped defaults, and the bound on how long one may go unchecked ------


def test_every_shipped_window_carries_a_dated_probe() -> None:
    """AC: a shipped default with no recorded provenance fails, today, on this tree.

    The live assertion. `ADAPTER_WINDOWS` is the only place a window may be written
    down here, so a figure added to a spec without one is what this catches.
    """
    shipped = {
        spec.name: spec.context_window
        for spec in BUILTIN_RUNNERS
        if spec.context_window_source == ADAPTER_WINDOW
    }
    assert shipped, "no adapter ships a window — the gate below would be inert"
    assert stale_declarations(shipped, today=datetime.now(tz=UTC).date()) == []


def test_a_window_shipped_with_no_recorded_probe_is_named() -> None:
    """The known-bad control: 400_000 for codex is exactly what was removed."""
    problems = stale_declarations({"codex": 400_000}, today=date(2026, 8, 15))

    assert len(problems) == 1
    assert "400,000" in problems[0]
    assert "who checked it or when" in problems[0]


def test_a_window_that_drifted_from_its_own_evidence_is_named() -> None:
    """Both figures, because the one to change is not the one the reader is looking at."""
    problems = stale_declarations({"claude": 200_000}, today=date(2026, 8, 15))

    assert len(problems) == 1
    assert "200,000" in problems[0] and "1,000,000" in problems[0]


def test_a_probe_past_the_recheck_bound_fails_and_names_the_reprobe() -> None:
    """The staleness half, with today injected rather than waited for.

    A window is a claim about a vendor's runtime, so it rots on a calendar whether or
    not a lane has recorded a contradiction yet — that is the whole gap `window_violations`
    leaves, since it needs a lane to have paid for the drift first.
    """
    entry = ADAPTER_WINDOWS["claude"]
    just_inside = entry.checked + timedelta(days=RECHECK_DAYS)
    just_past = just_inside + timedelta(days=1)

    assert stale_declarations({"claude": entry.tokens}, today=just_inside) == []
    problems = stale_declarations({"claude": entry.tokens}, today=just_past)
    assert len(problems) == 1
    assert entry.checked.isoformat() in problems[0]
    assert context_window.RECHECK_PROBE in problems[0]


# --- what a consumer's run record ends up saying ------------------------------


def _recorded(repo_root: Path, spec: RunnerSpec, stdout: str) -> dict:
    """One dispatch of *spec* returning *stdout*, as the ledger stores it."""
    result = RunResult(spec.name, spec.command, executed=True, returncode=0, stdout=stdout)
    runner.record_dispatch(repo_root, "basicly-89hm", spec, result, prompt="p", phase="lane")
    (entry,) = (run_record.load_run_records(repo_root) or {})["basicly-89hm"]
    return entry


def test_a_fresh_consumers_claude_dispatch_records_the_window_the_binary_reported(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC: no `[runner] context_windows` override, and no 200_000 in the record.

    The built-in claude adapter exactly as a consumer inherits it, against the captured
    dispatch. What lands is the window the adapter reported for the model it ran, so
    the pair on the row — occupancy against window — describes one dispatch.
    """
    monkeypatch.setattr(run_record, "record_marker", lambda *_a, **_k: None)
    claude = next(spec for spec in BUILTIN_RUNNERS if spec.name == "claude")
    assert claude.context_window_source == ADAPTER_WINDOW  # nothing declared, as installed

    entry = _recorded(tmp_path, claude, PROBE)

    assert entry["context_window"] == 1_000_000
    assert entry["context_window_source"] == OBSERVED_WINDOW


def test_a_dispatch_nothing_could_meter_says_so_on_the_record(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC: the engine records that it could not meter rather than assuming a default.

    codex reports no window, and this consumer declared none. The 400_000 the spec
    carries as an observation floor deliberately does not land here: reading it back
    as the denominator would be the same unchecked figure basicly-23ep was.
    """
    monkeypatch.setattr(run_record, "record_marker", lambda *_a, **_k: None)
    codex = next(spec for spec in BUILTIN_RUNNERS if spec.name == "codex")

    entry = _recorded(tmp_path, codex, '{"type":"turn.completed","usage":{"input_tokens":10}}')

    assert entry["context_window"] is None
    assert entry["context_window_source"] == UNMETERED


def test_the_recorded_evidence_names_the_field_it_was_read_from() -> None:
    """A date with no probe behind it is a claim about a claim."""
    for name, entry in ADAPTER_WINDOWS.items():
        assert isinstance(entry, AdapterWindow), name
        assert "contextWindow" in entry.evidence, name
        assert entry.tokens > 0, name

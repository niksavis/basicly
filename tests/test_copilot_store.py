"""Tests for the telemetry copilot reports out of band (`copilot_store`).

Copilot is the one family whose numbers are not in the captured output, so every test
here writes a session store on disk and asserts what is read back out of it — including
each way that read can fail (absent, unreadable, truncated, keyed by nothing), all of
which must degrade to a flagged estimate rather than raise. Asserted through
`runner.extract_usage`, the dispatcher that routes a copilot spec to this store.

Split out of `tests/test_runner.py` with the module they cover.
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import copilot_store, runner
from basicly.runner import BUILTIN_RUNNERS, RunnerSpec, RunResult

# Copied from a live copilot 1.0.75 session store on a developer box
# (`~/.copilot/session-state/<sessionId>/events.jsonl`, 2026-07-29) — the
# terminating `session.shutdown` event of a one-word probe, with its token
# counts, credits and metric shape verbatim. Only this one event was taken: the
# store's `user.message`/`assistant.message` events carry prompt and answer text
# and are never copied into a test.
#
# Redacted: the session UUID, replaced with a synthetic one. That is the join
# key — the store directory is named after it — so keeping the real value would
# both carry a developer's session identity and let a test that forgot to inject
# a store silently read the real one and still pass. Nothing else needed it:
# `codeChanges.filesModified` was already empty, the event holds no path, repo
# name or file content, and `claude-sonnet-5` is a plain public model name.
#
# The `session.usage_checkpoint` line above the shutdown is the same probe's real
# checkpoint, kept as the evidence for *why* the reader keys on shutdown: the
# checkpoint carries credits and no tokens at all.
_COPILOT_EVENTS = "\n".join([
    '{"type":"session.start","data":{"sessionId":"00000000-0000-4000-8000-000000000001",'
    '"copilotVersion":"1.0.75"}}',
    '{"type":"session.usage_checkpoint","data":{"totalPremiumRequests":1,'
    '"totalNanoAiu":6056400000,"modelCacheState":{}}}',
    '{"type":"session.shutdown","data":{"shutdownType":"routine","totalPremiumRequests":1,'
    '"totalNanoAiu":6056400000,"tokenDetails":{"input":{"tokenCount":2},'
    '"cache_read":{"tokenCount":0},"cache_write":{"tokenCount":24208},'
    '"output":{"tokenCount":4}},"totalApiDurationMs":1288,"sessionStartTime":1785353186397,'
    '"eventsFileSizeBytes":30642,"codeChanges":{"linesAdded":0,"linesRemoved":0,'
    '"filesModified":[]},"modelMetrics":{"claude-sonnet-5":{"requests":{"count":1,"cost":1},'
    '"usage":{"inputTokens":24210,"outputTokens":4,"cacheReadTokens":0,'
    '"cacheWriteTokens":24208,"reasoningTokens":0},"totalNanoAiu":6056400000,'
    '"tokenDetails":{"input":{"tokenCount":2},"cache_read":{"tokenCount":0},'
    '"cache_write":{"tokenCount":24208},"output":{"tokenCount":4}}}},'
    '"currentModel":"claude-sonnet-5","currentTokens":18217,"systemTokens":7107,'
    '"conversationTokens":79,"toolDefinitionsTokens":11027},'
    '"id":"3d927609-e21e-4009-9a6a-425fd19ed20c","timestamp":"2026-07-29T19:26:31.089Z",'
    '"parentId":"6d073fae-9717-4984-a4e3-237a29024a9f"}',
])

# Synthetic, and deliberately not a real session on any machine: a store lookup
# that escapes its tmp_path must miss, never quietly succeed against the
# developer's own `~/.copilot` (conftest hides the agent CLIs but not HOME).
_COPILOT_SESSION = "00000000-0000-4000-8000-000000000001"


def _copilot_spec(store: Path) -> RunnerSpec:
    """The copilot builtin, pointed at *store* instead of the developer's real one."""
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    return replace(copilot, session_store=store)


def _copilot_store(root: Path, events: str, session_id: str = _COPILOT_SESSION) -> Path:
    """Write *events* as a copilot session store under *root*, returning the base dir."""
    store = root / "session-state"
    (store / session_id).mkdir(parents=True)
    (store / session_id / "events.jsonl").write_text(events + "\n", encoding="utf-8")
    return store


def _copilot_run(spec: RunnerSpec, session_id: str | None = _COPILOT_SESSION) -> RunResult:
    """An executed copilot dispatch that keyed *session_id*, with plain-text stdout."""
    return RunResult(
        spec.name,
        (spec.name,),
        executed=True,
        returncode=0,
        stdout="done" * 25,
        session_id=session_id,
    )


def test_extract_usage_copilot_reads_the_shutdown_model_metrics(tmp_path: Path) -> None:
    """The store's session.shutdown yields the measured split, credits, and total.

    Pinned against the captured 1.0.75 event: `inputTokens` already contains both
    cache counts, so the total is input + output — adding the cache fields would
    report 48K for a 24K probe.
    """
    spec = _copilot_spec(_copilot_store(tmp_path, _COPILOT_EVENTS))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is False
    assert (usage.input_tokens, usage.output_tokens) == (24210, 4)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (0, 24208)
    assert usage.reasoning_tokens == 0
    assert usage.tokens == 24210 + 4
    # nanoAiu -> credits, and cost stays null: credits are not USD.
    assert usage.credits == pytest.approx(6.0564)
    assert usage.cost is None


def test_extract_usage_copilot_sums_across_models(tmp_path: Path) -> None:
    """A dispatch that switched model mid-run meters once, over every model block."""
    events = json.dumps({
        "type": "session.shutdown",
        "data": {
            "modelMetrics": {
                "claude-sonnet-5": {
                    "usage": {
                        "inputTokens": 100,
                        "outputTokens": 10,
                        "cacheReadTokens": 60,
                        "cacheWriteTokens": 30,
                        "reasoningTokens": 4,
                    },
                    "totalNanoAiu": 1_500_000_000,
                },
                "gpt-5": {
                    "usage": {
                        "inputTokens": 200,
                        "outputTokens": 20,
                        "cacheReadTokens": 150,
                        "cacheWriteTokens": 40,
                        "reasoningTokens": 6,
                    },
                    "totalNanoAiu": 500_000_000,
                },
            }
        },
    })
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (300, 30)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (210, 70)
    assert usage.reasoning_tokens == 10
    assert usage.tokens == 330
    assert usage.credits == pytest.approx(2.0)


def test_extract_usage_copilot_skips_noise_and_a_truncated_tail(tmp_path: Path) -> None:
    """Unparseable and unrecognized lines are skipped, not treated as a parse failure.

    A killed dispatch leaves a truncated final line, and the store interleaves
    event kinds the reader knows nothing about.
    """
    events = "\n".join([
        "not json at all",
        _COPILOT_EVENTS,
        '{"type":"session.shutdown","data":{"modelMe',  # truncated tail
    ])
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.tokens == 24210 + 4
    assert usage.estimated is False


def test_extract_usage_copilot_absent_store_falls_back_to_the_estimate(tmp_path: Path) -> None:
    """No store on disk meters by estimate, *flagged* as one — never as measured."""
    spec = _copilot_spec(tmp_path / "session-state")
    result = _copilot_run(spec)
    usage = runner.extract_usage(spec, result)
    assert usage is not None
    assert usage == runner.Usage(tokens=len(result.stdout) // 4, cost=None, estimated=True)
    assert usage.credits is None and usage.input_tokens is None


def test_extract_usage_copilot_unreadable_store_falls_back_to_the_estimate(
    tmp_path: Path,
) -> None:
    """A store path that is a directory, not a readable file, degrades the same way."""
    store = tmp_path / "session-state"
    (store / _COPILOT_SESSION / "events.jsonl").mkdir(parents=True)  # not a file
    spec = _copilot_spec(store)
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_copilot_without_a_session_id_estimates(tmp_path: Path) -> None:
    """No store key means nothing to join on, so the run meters by estimate.

    The store on disk is real here: the point is that it is *not* read, because
    guessing which session was this dispatch's would attribute another run's spend.
    """
    spec = _copilot_spec(_copilot_store(tmp_path, _COPILOT_EVENTS))
    usage = runner.extract_usage(spec, _copilot_run(spec, session_id=None))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_copilot_store_without_a_shutdown_event_estimates(tmp_path: Path) -> None:
    """A session killed before shutdown has no metrics, so it meters by estimate.

    This is why the usage_checkpoint event is not the source: it survives a kill
    but carries credits and no tokens, which would report a token-free dispatch.
    """
    events = "\n".join([
        '{"type":"session.start","data":{"sessionId":"' + _COPILOT_SESSION + '"}}',
        '{"type":"session.usage_checkpoint","data":{"totalNanoAiu":6056400000}}',
    ])
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_copilot_shutdown_without_usable_metrics_estimates(tmp_path: Path) -> None:
    """A shutdown event whose model metrics carry no token count degrades, not zeroes."""
    events = json.dumps({
        "type": "session.shutdown",
        "data": {"modelMetrics": {"claude-sonnet-5": {"requests": {"count": 1}}}},
    })
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is True


def test_copilot_session_store_default_is_home_relative_and_unexpanded() -> None:
    """The default never bakes in a machine path and never calls home() at import.

    An absolute default resolved at import time would be a committed
    machine-specific path, and would make the suite read the developer's real
    store whenever a test forgot to inject one.
    """
    assert Path("~/.copilot/session-state") == copilot_store.DEFAULT_COPILOT_SESSION_STORE
    assert next(s for s in BUILTIN_RUNNERS if s.name == "copilot").session_store is None


def test_extract_usage_copilot_expands_a_home_relative_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A `~`-relative store base is expanded at read time, so `~` stays portable."""
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))  # Path.expanduser on Windows
    _copilot_store(tmp_path / ".copilot", _COPILOT_EVENTS)
    spec = _copilot_spec(Path("~/.copilot/session-state"))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.tokens == 24210 + 4

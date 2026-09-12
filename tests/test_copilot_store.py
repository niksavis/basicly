from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path

import pytest

from basicly import copilot_store, runner
from basicly.runner import BUILTIN_RUNNERS, RunnerSpec, RunResult

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

_COPILOT_SESSION = "00000000-0000-4000-8000-000000000001"


def _copilot_spec(store: Path) -> RunnerSpec:
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    return replace(copilot, session_store=store)


def _copilot_store(root: Path, events: str, session_id: str = _COPILOT_SESSION) -> Path:
    store = root / "session-state"
    (store / session_id).mkdir(parents=True)
    (store / session_id / "events.jsonl").write_text(events + "\n", encoding="utf-8")
    return store


def _copilot_run(spec: RunnerSpec, session_id: str | None = _COPILOT_SESSION) -> RunResult:
    return RunResult(
        spec.name,
        (spec.name,),
        executed=True,
        returncode=0,
        stdout="done" * 25,
        session_id=session_id,
    )


def test_extract_usage_copilot_reads_the_shutdown_model_metrics(tmp_path: Path) -> None:

    spec = _copilot_spec(_copilot_store(tmp_path, _COPILOT_EVENTS))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is False
    assert (usage.input_tokens, usage.output_tokens) == (24210, 4)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (0, 24208)
    assert usage.reasoning_tokens == 0
    assert usage.tokens == 24210 + 4
    assert usage.credits == pytest.approx(6.0564)
    assert usage.cost is None


def test_extract_usage_copilot_sums_across_models(tmp_path: Path) -> None:
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

    events = "\n".join([
        "not json at all",
        _COPILOT_EVENTS,
        '{"type":"session.shutdown","data":{"modelMe',
    ])
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.tokens == 24210 + 4
    assert usage.estimated is False


def test_extract_usage_copilot_absent_store_falls_back_to_the_estimate(tmp_path: Path) -> None:
    spec = _copilot_spec(tmp_path / "session-state")
    result = _copilot_run(spec)
    usage = runner.extract_usage(spec, result)
    assert usage is not None
    assert usage == runner.Usage(tokens=len(result.stdout) // 4, cost=None, estimated=True)
    assert usage.credits is None and usage.input_tokens is None


def test_extract_usage_copilot_unreadable_store_falls_back_to_the_estimate(
    tmp_path: Path,
) -> None:
    store = tmp_path / "session-state"
    (store / _COPILOT_SESSION / "events.jsonl").mkdir(parents=True)
    spec = _copilot_spec(store)
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_copilot_without_a_session_id_estimates(tmp_path: Path) -> None:

    spec = _copilot_spec(_copilot_store(tmp_path, _COPILOT_EVENTS))
    usage = runner.extract_usage(spec, _copilot_run(spec, session_id=None))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_copilot_store_without_a_shutdown_event_estimates(tmp_path: Path) -> None:

    events = "\n".join([
        '{"type":"session.start","data":{"sessionId":"' + _COPILOT_SESSION + '"}}',
        '{"type":"session.usage_checkpoint","data":{"totalNanoAiu":6056400000}}',
    ])
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_copilot_shutdown_without_usable_metrics_estimates(tmp_path: Path) -> None:
    events = json.dumps({
        "type": "session.shutdown",
        "data": {"modelMetrics": {"claude-sonnet-5": {"requests": {"count": 1}}}},
    })
    spec = _copilot_spec(_copilot_store(tmp_path, events))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.estimated is True


def test_copilot_session_store_default_is_home_relative_and_unexpanded() -> None:

    assert Path("~/.copilot/session-state") == copilot_store.DEFAULT_COPILOT_SESSION_STORE
    assert next(s for s in BUILTIN_RUNNERS if s.name == "copilot").session_store is None


def test_extract_usage_copilot_expands_a_home_relative_store(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    _copilot_store(tmp_path / ".copilot", _COPILOT_EVENTS)
    spec = _copilot_spec(Path("~/.copilot/session-state"))
    usage = runner.extract_usage(spec, _copilot_run(spec))
    assert usage is not None
    assert usage.tokens == 24210 + 4

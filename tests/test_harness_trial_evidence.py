from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import runner
from tests.test_harness_trial_runner import case, identity, load_trial_runner


@pytest.mark.parametrize("observed", ["claude-opus-5-5", "claude-sonnet-5-5"])
def test_harness_trial_runner_checks_the_observed_model_before_acceptance(
    tmp_path: Path, observed: str
) -> None:
    requested = "claude-opus-5-5"
    event = {"type": "result", "modelUsage": {observed: {}}}
    script = (
        "from pathlib import Path; Path('value.txt').write_text('after'); "
        f"print({json.dumps(event)!r})"
    )
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=runner.CLAUDE_JSON,
    )

    report = load_trial_runner().execute_trial(
        spec, case(), tmp_path / "one", {**identity(), "model": requested}
    )

    assert report["observed_models"] == [observed]
    assert (report["model_mismatch"] is None) == (observed == requested)
    assert report["outcome"] == ("passed" if observed == requested else "infrastructure_error")
    assert report["acceptance"]["outcome"] == ("passed" if observed == requested else "failed")


@pytest.mark.parametrize("fallback", [False, True])
def test_harness_trial_runner_distinguishes_owner_fallback_from_other_model_usage(
    tmp_path: Path, fallback: bool
) -> None:
    requested = "claude-opus-5-5"
    other = "claude-fable-5-2"
    events = [
        {"type": "assistant", "message": {"model": requested, "content": []}},
        {
            "type": "assistant",
            "parent_tool_use_id": "worker",
            "message": {"model": "claude-sonnet-5-5", "content": []},
        },
        {
            "type": "assistant",
            "message": {"model": other if fallback else requested, "content": []},
        },
        {"type": "result", "modelUsage": {requested: {}, other: {}}},
    ]
    output = "\n".join(json.dumps(event) for event in events)
    script = f"from pathlib import Path; Path('value.txt').write_text('after'); print({output!r})"
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=runner.CLAUDE_STREAM_JSON,
    )

    report = load_trial_runner().execute_trial(
        spec, case(), tmp_path / "one", {**identity(), "model": requested}
    )

    assert report["model_verified"] is not fallback
    assert report["acceptance"]["outcome"] == ("failed" if fallback else "passed")


@pytest.mark.parametrize("conflicting", [False, True])
@pytest.mark.parametrize(
    "usage_format,event_kind,key",
    [
        (runner.CODEX_JSONL, "thread.started", "thread_id"),
        (runner.CLAUDE_JSON, "result", "session_id"),
        (runner.CLAUDE_STREAM_JSON, "result", "session_id"),
        (runner.CLAUDE_STREAM_JSON, "system", "session_id"),
    ],
)
def test_harness_trial_runner_records_unambiguous_native_session(
    tmp_path: Path, conflicting: bool, usage_format: str, event_kind: str, key: str
) -> None:
    session = "00000000-0000-0000-0000-000000000001"
    events = [{"type": event_kind, "subtype": "init", key: session}]
    if conflicting:
        events.append({
            "type": event_kind,
            "subtype": "init",
            key: "00000000-0000-0000-0000-000000000002",
        })
    script = (
        "import json; from pathlib import Path; Path('value.txt').write_text('after'); "
        f"[print(json.dumps(event)) for event in {events!r}]"
    )
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=usage_format,
    )
    report = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    if conflicting:
        assert report["outcome"] == "infrastructure_error"
        assert "conflicting native session" in report["error"]
    else:
        assert report["outcome"] == "passed"
        assert report["session_id"] == session


@pytest.mark.parametrize("session", ["", 42, "not-a-session-id"])
def test_harness_trial_runner_rejects_malformed_native_session(
    tmp_path: Path, session: Any
) -> None:
    script = (
        "import json; from pathlib import Path; Path('value.txt').write_text('after'); "
        f"print(json.dumps({{'type': 'thread.started', 'thread_id': {session!r}}}))"
    )
    spec = runner.RunnerSpec(
        "fixture",
        command=(sys.executable, "-c", script, runner.PROMPT_PLACEHOLDER),
        usage_format=runner.CODEX_JSONL,
    )
    report = load_trial_runner().execute_trial(spec, case(), tmp_path / "one", identity())
    assert report["outcome"] == "infrastructure_error"
    assert "invalid native session identifier" in report["error"]
    assert report["acceptance"]["outcome"] == "passed"

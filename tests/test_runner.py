from __future__ import annotations

import itertools
import json
import os
import signal
import subprocess
import sys
import threading
import time
import uuid
from collections.abc import Callable
from dataclasses import replace
from pathlib import Path
from typing import cast

import pytest

from basicly import context_window, copilot_store, models, run_record, runner, runner_envelope
from basicly.config import load_runner_config
from basicly.runner import (
    BUILTIN_RUNNERS,
    CLAUDE_JSON,
    CLAUDE_STREAM_JSON,
    CODEX_JSONL,
    COPILOT_SESSION_STORE,
    HANDOFF,
    HEADLESS,
    MANUAL_RUNNER,
    PROMPT_PLACEHOLDER,
    RunnerSpec,
    RunResult,
)

REPO_ROOT = Path(__file__).resolve().parents[1]


def _which_none(_binary: str) -> str | None:
    return None


def _which_only(*available: str):
    def which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary in available else None

    return which


def test_format_command_injects_prompt_as_arg() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do the thing") == ["claude", "-p", "do the thing"]


def test_format_command_stdin_keeps_prompt_out_of_argv() -> None:
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin")
    assert runner.format_command(spec, "prompt text") == ["x", "--headless"]


def test_format_command_rejects_handoff() -> None:
    with pytest.raises(ValueError, match="not headless"):
        runner.format_command(RunnerSpec(MANUAL_RUNNER, HANDOFF), "p")


def test_format_command_rejects_arg_template_without_placeholder() -> None:
    spec = RunnerSpec("bad", HEADLESS, ("bad", "run"))
    with pytest.raises(ValueError, match="placeholder"):
        runner.format_command(spec, "p")


def test_format_command_no_model_leaves_argv_unchanged() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do it") == ["claude", "-p", "do it"]


def test_format_command_injects_model_after_binary() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), model="opus")
    assert runner.format_command(spec, "do it") == ["claude", "--model", "opus", "-p", "do it"]


def test_format_command_substitutes_model_placeholder() -> None:
    spec = RunnerSpec(
        "acme",
        HEADLESS,
        ("acme", "--llm", runner.MODEL_PLACEHOLDER, "run", PROMPT_PLACEHOLDER),
        model="fast-1",
    )
    assert runner.format_command(spec, "go") == ["acme", "--llm", "fast-1", "run", "go"]


def test_format_command_model_placeholder_without_model_raises() -> None:
    spec = RunnerSpec(
        "acme", HEADLESS, ("acme", "--llm", runner.MODEL_PLACEHOLDER, PROMPT_PLACEHOLDER)
    )
    with pytest.raises(ValueError, match="no model is set"):
        runner.format_command(spec, "go")


def test_format_command_injects_model_for_stdin_runner() -> None:
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin", model="m1")
    assert runner.format_command(spec, "ignored") == ["x", "--model", "m1", "--headless"]


def test_format_command_no_deny_tools_leaves_argv_unchanged() -> None:
    spec = RunnerSpec("copilot", HEADLESS, ("copilot", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do it") == ["copilot", "-p", "do it"]


def test_format_command_injects_deny_tool_flags_after_binary() -> None:
    spec = RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        deny_tools=("shell(rm -rf)", "shell(git push --force)"),
        deny_style=runner.DENY_TOOL_FLAG,
    )
    assert runner.format_command(spec, "go") == [
        "copilot",
        "--deny-tool=shell(rm -rf)",
        "--deny-tool=shell(git push --force)",
        "-p",
        "go",
    ]


def test_format_command_deny_tools_compose_after_model() -> None:
    spec = RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        model="fast",
        deny_tools=("write",),
        deny_style=runner.DENY_TOOL_FLAG,
    )
    assert runner.format_command(spec, "go") == [
        "copilot",
        "--model",
        "fast",
        "--deny-tool=write",
        "-p",
        "go",
    ]


def test_format_command_no_sandbox_or_approval_leaves_argv_unchanged() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do it") == ["claude", "-p", "do it"]


def test_format_command_injects_sandbox_and_approval_after_binary() -> None:
    spec = RunnerSpec(
        "codex",
        HEADLESS,
        ("codex", "exec", PROMPT_PLACEHOLDER),
        sandbox="workspace-write",
        approval="never",
    )
    assert runner.format_command(spec, "go") == [
        "codex",
        "--sandbox",
        "workspace-write",
        "-a",
        "never",
        "exec",
        "go",
    ]


def test_format_command_injects_sandbox_alone() -> None:
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER), sandbox="read-only")
    assert runner.format_command(spec, "go") == ["codex", "--sandbox", "read-only", "exec", "go"]


def test_codex_builtin_defaults_render_workspace_write_never() -> None:

    codex = next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")
    assert codex.sandbox == "workspace-write"
    assert codex.approval == "never"
    assert runner.format_command(codex, "do the work") == [
        "codex",
        "--sandbox",
        "workspace-write",
        "-a",
        "never",
        "exec",
        "do the work",
    ]


def test_sandbox_approval_do_not_affect_capability_probe() -> None:

    codex = next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")
    cap = runner.probe_capability(codex, run=lambda _binary: "usage: codex exec [prompt]")
    assert cap.flag_ok is True


def _builtin(name: str) -> RunnerSpec:
    return next(s for s in runner.BUILTIN_RUNNERS if s.name == name)


CODEX_HELP = """\
Usage: codex [OPTIONS] [PROMPT]

Options:
  -s, --sandbox <SANDBOX_MODE>
          Select the sandbox policy to use when executing model-generated shell commands

          [possible values: read-only, workspace-write, danger-full-access]

      --add-dir <DIR>
          Additional directories that should be writable alongside the primary workspace

  -a, --ask-for-approval <APPROVAL_POLICY>
          Configure when the model requires human approval before executing a command

          Possible values:
          - untrusted:  Only run "trusted" commands (e.g. ls, cat, sed) without asking for user
            approval. Will escalate to the user if the model proposes a command that is not in the
            "trusted" set
          - on-request: The model decides when to ask the user for approval
          - never:      Never ask for user approval Execution failures are immediately returned to
            the model

  -h, --help
          Print help
"""


def test_possible_values_reads_the_inline_enum_rendering() -> None:
    assert runner.possible_values(CODEX_HELP, "--sandbox") == (
        "read-only",
        "workspace-write",
        "danger-full-access",
    )


def test_possible_values_reads_the_bulleted_enum_rendering() -> None:

    assert runner.possible_values(CODEX_HELP, "--ask-for-approval") == (
        "untrusted",
        "on-request",
        "never",
    )


def test_possible_values_is_none_when_the_flag_enumerates_nothing() -> None:
    assert runner.possible_values(CODEX_HELP, "--no-such-flag") is None
    assert runner.possible_values(CODEX_HELP, "--add-dir") is None


def test_check_guardrails_names_the_rejected_approval_and_the_accepted_set() -> None:

    spec = replace(_builtin("codex"), approval="on-failure")
    (problem,) = runner.check_guardrails(spec, help_text=CODEX_HELP)
    assert "on-failure" in problem
    assert "--ask-for-approval" in problem
    assert "untrusted, on-request, never" in problem


def test_check_guardrails_names_a_rejected_sandbox() -> None:
    spec = replace(_builtin("codex"), sandbox="wide-open")
    (problem,) = runner.check_guardrails(spec, help_text=CODEX_HELP)
    assert "wide-open" in problem
    assert "read-only, workspace-write, danger-full-access" in problem


def test_check_guardrails_passes_the_shipped_codex_spec() -> None:
    assert runner.check_guardrails(_builtin("codex"), help_text=CODEX_HELP) == ()


def test_check_guardrails_stays_silent_without_positive_evidence() -> None:

    codex = _builtin("codex")
    assert runner.check_guardrails(codex, help_text=None) == ()
    assert runner.check_guardrails(codex, help_text="usage: codex exec [prompt]") == ()
    assert runner.probe_guardrails(codex, run=lambda _binary: None) == ()


def test_probe_capability_fails_a_spec_the_cli_would_reject() -> None:

    spec = replace(_builtin("codex"), approval="on-failure")
    cap = runner.probe_capability(spec, run=lambda _binary: CODEX_HELP)
    assert cap.reachable is True
    assert cap.flag_ok is False
    assert "on-failure" in cap.detail
    capable = runner.is_capable(spec, which=lambda _b: "/usr/bin/codex", run=lambda _b: CODEX_HELP)
    assert capable is False


def test_confine_for_decider_denies_claudes_whole_tool_surface() -> None:
    confined = runner.confine_for_decider(_builtin("claude"))
    assert confined is not None
    argv = runner.format_command(confined, "judge")
    assert argv[0] == "claude"
    assert argv[1] == "--disallowedTools"
    denied = set(argv[2 : argv.index("-p")])
    assert {"Bash", "Write", "Read"} <= denied
    assert "judge" in argv


def test_confine_for_decider_denies_copilot_shell_write_and_read() -> None:

    confined = runner.confine_for_decider(_builtin("copilot"))
    assert confined is not None
    assert "read" in confined.deny_tools
    argv = runner.format_command(confined, "judge")
    assert argv[:4] == [
        "copilot",
        "--deny-tool=shell",
        "--deny-tool=write",
        "--deny-tool=read",
    ]


def test_confine_for_decider_puts_codex_in_a_read_only_sandbox() -> None:

    confined = runner.confine_for_decider(_builtin("codex"))
    assert confined is not None
    assert confined.deny_tools == ()
    assert runner.format_command(confined, "judge") == [
        "codex",
        "--sandbox",
        "read-only",
        "-a",
        "never",
        "exec",
        "judge",
    ]


def test_confine_for_decider_adds_to_existing_denials_never_replaces_them() -> None:

    baseline = RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        deny_tools=("fetch", "shell"),
        deny_style=runner.DENY_TOOL_FLAG,
    )
    confined = runner.confine_for_decider(baseline)
    assert confined is not None
    assert confined.deny_tools == ("fetch", "shell", "write", "read")


def test_confine_for_decider_refuses_an_unconfinable_family() -> None:

    unknown = RunnerSpec("mystery", HEADLESS, ("mystery", PROMPT_PLACEHOLDER))
    assert runner.confine_for_decider(unknown) is None


def test_confine_for_decider_leaves_a_handoff_unchanged() -> None:
    handoff = RunnerSpec(MANUAL_RUNNER, HANDOFF)
    assert runner.confine_for_decider(handoff) is handoff


def test_deny_tools_without_a_style_raises_rather_than_emitting_a_flag() -> None:
    spec = RunnerSpec("mystery", HEADLESS, ("mystery", PROMPT_PLACEHOLDER), deny_tools=("write",))
    with pytest.raises(ValueError, match="deny_style"):
        runner.format_command(spec, "go")


def test_is_available_handoff_is_always_true() -> None:
    assert runner.is_available(RunnerSpec(MANUAL_RUNNER, HANDOFF), which=_which_none) is True


def test_is_available_headless_follows_path() -> None:
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER))
    assert runner.is_available(spec, which=_which_only("codex")) is True
    assert runner.is_available(spec, which=_which_none) is False


def test_select_explicit_name_wins() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, "codex", which=_which_none)
    assert spec.name == "codex"


def test_select_explicit_unknown_raises() -> None:
    with pytest.raises(ValueError, match="unknown runner"):
        runner.select_runner(BUILTIN_RUNNERS, "nope", which=_which_none)


def test_auto_prefers_first_available_in_order() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", which=_which_only("codex", "copilot"))
    assert spec.name == "codex"


def test_auto_falls_back_to_manual_handoff_when_none_present() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", which=_which_none)
    assert spec.name == MANUAL_RUNNER
    assert spec.kind == HANDOFF


def test_none_choice_behaves_like_auto() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, None, which=_which_only("claude"))
    assert spec.name == "claude"


def test_auto_resolves_ambiently_to_the_handoff_on_every_machine() -> None:

    spec = runner.select_runner(BUILTIN_RUNNERS, "auto")
    assert spec.name == MANUAL_RUNNER
    assert spec.kind == HANDOFF
    result = runner.run(spec, "do the work", Path())
    assert result.handoff is True
    assert result.executed is False
    assert result.returncode is None


def test_headless_flags_excludes_placeholders() -> None:
    spec = RunnerSpec(
        "acme", HEADLESS, ("acme", "run", runner.MODEL_PLACEHOLDER, PROMPT_PLACEHOLDER)
    )
    assert runner._headless_flags(spec) == ["run"]


def test_probe_capability_confirms_a_present_flag() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    cap = runner.probe_capability(spec, run=lambda _b: "usage: claude [-p, --print] ...")
    assert cap.reachable and cap.flag_ok


def test_probe_capability_flags_a_dropped_flag() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    cap = runner.probe_capability(spec, run=lambda _b: "usage: claude [--chat] (no print flag)")
    assert cap.reachable and not cap.flag_ok
    assert "-p" in cap.detail


def test_probe_capability_assumes_capable_when_unprobeable() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    cap = runner.probe_capability(spec, run=lambda _b: None)
    assert cap.reachable is False and cap.flag_ok is True


def test_probe_capability_handoff_is_trivially_capable() -> None:
    assert runner.probe_capability(RunnerSpec(MANUAL_RUNNER, HANDOFF)).flag_ok is True


def test_is_capable_requires_both_path_and_flag() -> None:
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER))
    assert runner.is_capable(spec, which=_which_only("codex"), run=lambda _b: "codex exec ...")
    assert not runner.is_capable(spec, which=_which_only("codex"), run=lambda _b: "codex chat")
    assert not runner.is_capable(spec, which=_which_none, run=lambda _b: "codex exec")


def test_auto_skips_an_incapable_runner() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", capable=lambda s: s.name == "codex")
    assert spec.name == "codex"


def test_auto_falls_back_to_manual_when_none_capable() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", capable=lambda _s: False)
    assert spec.name == MANUAL_RUNNER


def test_explicit_choice_is_not_probe_gated() -> None:
    spec = runner.select_runner(BUILTIN_RUNNERS, "claude", capable=lambda _s: False)
    assert spec.name == "claude"


def test_run_dry_run_returns_argv_without_executing(monkeypatch: pytest.MonkeyPatch) -> None:

    def boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called on a dry run")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "hello", Path("/tmp"), dry_run=True)
    assert result.executed is False
    assert result.command == ("claude", "-p", "hello")
    assert result.duration_s is None


def test_run_handoff_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:

    def boom(*_a, **_k):
        raise AssertionError("a handoff runner must not execute anything")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    result = runner.run(RunnerSpec(MANUAL_RUNNER, HANDOFF), "hello", Path("/tmp"))
    assert result.handoff is True
    assert result.executed is False
    assert result.command == ()
    assert result.duration_s is None


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> dict[str, object]:

    captured: dict[str, object] = {}

    class _Proc:
        pid = 1234

        def __init__(self) -> None:
            self.returncode = returncode

        def communicate(self, input=None, timeout=None):  # noqa: A002 — Popen's own name
            captured["input"] = input
            captured["timeout"] = timeout
            return stdout, stderr

    def fake_popen(argv, **kwargs):
        captured["argv"] = argv
        captured.update(kwargs)
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "Popen", fake_popen)
    return captured


def test_run_executes_and_captures(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_popen(monkeypatch, stdout="done")
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "build it", Path("/work"))

    assert captured["argv"] == ["claude", "-p", "build it"]
    assert captured["cwd"] == Path("/work")
    assert captured["input"] is None
    assert result.executed is True
    assert result.returncode == 0
    assert result.stdout == "done"
    assert isinstance(result.duration_s, float) and result.duration_s >= 0


def test_run_redacts_secrets_from_captured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    token = "ghp_" + "a" * 30
    _patch_popen(monkeypatch, stdout=f"pushed with {token}", stderr=f"warning near {token}")
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "go", Path("/work"))

    assert token not in result.stdout and "<redacted:github-token>" in result.stdout
    assert token not in result.stderr and "<redacted:github-token>" in result.stderr


def test_run_stdin_injection_passes_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_popen(monkeypatch)
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin")
    runner.run(spec, "prompt on stdin", Path("/work"))

    assert captured["argv"] == ["x", "--headless"]
    assert captured["input"] == "prompt on stdin"
    assert captured["stdin"] is subprocess.PIPE


def test_run_arg_prompt_closes_stdin(monkeypatch: pytest.MonkeyPatch) -> None:

    captured = _patch_popen(monkeypatch)
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER))
    runner.run(spec, "do the thing", Path("/work"))

    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["input"] is None


def test_git_identity_env_none_without_identity() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.git_identity_env(spec) is None


def test_git_identity_env_pins_all_four_vars() -> None:
    spec = RunnerSpec(
        "bot",
        HEADLESS,
        ("bot", "-p", PROMPT_PLACEHOLDER),
        git_name="basicly-bot",
        git_email="bot@example.com",
    )
    assert runner.git_identity_env(spec) == {
        "GIT_AUTHOR_NAME": "basicly-bot",
        "GIT_AUTHOR_EMAIL": "bot@example.com",
        "GIT_COMMITTER_NAME": "basicly-bot",
        "GIT_COMMITTER_EMAIL": "bot@example.com",
    }


def test_run_injects_bot_identity_env(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_popen(monkeypatch)
    monkeypatch.setenv("EXISTING_VAR", "kept")
    spec = RunnerSpec(
        "bot",
        HEADLESS,
        ("bot", "-p", PROMPT_PLACEHOLDER),
        git_name="basicly-bot",
        git_email="bot@example.com",
    )
    runner.run(spec, "go", Path("/work"))

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["GIT_AUTHOR_NAME"] == "basicly-bot"
    assert env["GIT_AUTHOR_EMAIL"] == "bot@example.com"
    assert env["GIT_COMMITTER_NAME"] == "basicly-bot"
    assert env["GIT_COMMITTER_EMAIL"] == "bot@example.com"
    assert env["EXISTING_VAR"] == "kept"


def test_run_without_identity_adds_only_attribution(monkeypatch: pytest.MonkeyPatch) -> None:

    monkeypatch.delenv("VIRTUAL_ENV", raising=False)
    captured = _patch_popen(monkeypatch)
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    runner.run(spec, "go", Path("/work"))

    env = captured["env"]
    assert isinstance(env, dict)
    assert env["BR_AGENT_NAME"] == "claude"

    def strip(mapping: dict) -> dict:
        return {
            k: v for k, v in mapping.items() if k not in ("BR_AGENT_NAME", "BR_HARNESS", "BR_MODEL")
        }

    assert strip(env) == strip(dict(os.environ))


_CLAUDE_STREAM_FLAGS = ["--output-format", "stream-json", "--verbose", "--forward-subagent-text"]


def _claude_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "claude")


def _claude_json_spec() -> RunnerSpec:
    return _claude_spec().__class__(
        "claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), usage_format=CLAUDE_JSON
    )


def _codex_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "codex")


def _executed(spec: RunnerSpec, stdout: str, stderr: str = "") -> RunResult:
    return RunResult(
        spec.name, (spec.name,), executed=True, returncode=0, stdout=stdout, stderr=stderr
    )


_CLAUDE_RESULT = json.dumps({
    "type": "result",
    "subtype": "success",
    "is_error": False,
    "result": "ok",
    "total_cost_usd": 0.136147,
    "usage": {
        "input_tokens": 2,
        "cache_creation_input_tokens": 5960,
        "cache_read_input_tokens": 15496,
        "output_tokens": 17,
        "server_tool_use": {"web_search_requests": 0},
    },
})

_CLAUDE_STREAM = "\n".join([
    "Warning: no stdin data received in 3s, proceeding without it.",
    '{"type":"system","subtype":"init","tools":[]}',
    '{"type":"rate_limit_event","rate_limit_info":{"status":"allowed"}}',
    '{"type":"assistant","message":{"usage":{"input_tokens":4,'
    '"cache_creation_input_tokens":5960,"cache_read_input_tokens":0,"output_tokens":91}}}',
    '{"type":"user","message":{"content":"tool result"}}',
    '{"type":"assistant","message":{"usage":{"input_tokens":2,'
    '"cache_creation_input_tokens":40,"cache_read_input_tokens":15496,"output_tokens":17}}}',
    json.dumps({
        "type": "result",
        "subtype": "success",
        "result": "ok",
        "total_cost_usd": 0.136147,
        "usage": {
            "input_tokens": 6,
            "cache_creation_input_tokens": 6000,
            "cache_read_input_tokens": 15496,
            "output_tokens": 108,
        },
    }),
])

_CODEX_TURNS = (
    (
        {
            "input_tokens": 12764,
            "cached_input_tokens": 9984,
            "cache_write_input_tokens": 0,
            "output_tokens": 155,
            "reasoning_output_tokens": 147,
        },
        12919,
    ),
    (
        {
            "input_tokens": 16824,
            "cached_input_tokens": 10496,
            "cache_write_input_tokens": 0,
            "output_tokens": 5,
            "reasoning_output_tokens": 0,
        },
        16829,
    ),
)


def _codex_stream(*usages: dict) -> str:

    lines = ['{"type":"thread.started","thread_id":"t1"}']
    for usage in usages:
        lines.append('{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}')
        lines.append(json.dumps({"type": "turn.completed", "usage": usage}))
    return "\n".join(lines)


_CODEX_EVENTS = _codex_stream(*(usage for usage, _total in _CODEX_TURNS))


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
_PROMPT = "p"


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
        tuple(runner.format_command(spec, _PROMPT, capture_usage=True)),
        executed=True,
        returncode=0,
        stdout="done" * 25,
        session_id=session_id,
    )


def test_format_command_default_omits_usage_flags() -> None:
    assert runner.format_command(_claude_spec(), "go") == ["claude", "-p", "go"]


def test_format_command_capture_usage_appends_claude_flags() -> None:

    argv = runner.format_command(_claude_spec(), "go", capture_usage=True)
    assert argv == ["claude", "-p", "go", *_CLAUDE_STREAM_FLAGS]


def test_format_command_capture_usage_keeps_the_pinned_json_envelope() -> None:
    argv = runner.format_command(_claude_json_spec(), "go", capture_usage=True)
    assert argv == ["claude", "-p", "go", "--output-format", "json"]


def test_format_command_capture_usage_appends_codex_json_trailing() -> None:
    argv = runner.format_command(_codex_spec(), "go", capture_usage=True)
    assert argv[-1] == "--json"
    assert argv.index("exec") < argv.index("--json")


def test_format_command_capture_usage_without_format_leaves_argv_unchanged() -> None:
    spec = RunnerSpec("acme", HEADLESS, ("acme", PROMPT_PLACEHOLDER))
    assert spec.usage_format is None
    argv = runner.format_command(spec, "go", capture_usage=True)
    assert argv == runner.format_command(spec, "go")


def test_format_command_unknown_usage_format_raises() -> None:
    spec = RunnerSpec("x", HEADLESS, ("x", PROMPT_PLACEHOLDER), usage_format="bogus")
    with pytest.raises(ValueError, match="usage_format"):
        runner.format_command(spec, "go", capture_usage=True)


def test_usage_format_does_not_affect_capability_probe() -> None:
    cap = runner.probe_capability(_claude_spec(), run=lambda _binary: "usage: claude -p [prompt]")
    assert cap.flag_ok is True


def test_run_capture_usage_executes_with_usage_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    captured = _patch_popen(monkeypatch)
    runner.run(_claude_spec(), "go", Path("/work"), capture_usage=True)
    assert captured["argv"] == ["claude", "-p", "go", *_CLAUDE_STREAM_FLAGS]


def test_format_command_capture_usage_keys_the_copilot_session_store() -> None:

    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    argv = runner.format_command(copilot, "go", capture_usage=True, session_id="sid-1")
    assert argv[-2:] == ["--session-id", "sid-1"]
    assert "--output-format" not in argv
    assert argv[:-2] == runner.format_command(copilot, "go")


def test_format_command_copilot_without_a_session_id_omits_the_flag() -> None:

    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    argv = runner.format_command(copilot, "go", capture_usage=True)
    assert argv == runner.format_command(copilot, "go")


def test_run_mints_a_session_id_for_a_metered_copilot_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    captured = _patch_popen(monkeypatch)
    result = runner.run(copilot, "go", Path("/work"), capture_usage=True)
    assert result.session_id is not None
    argv = cast("list[str]", captured["argv"])
    assert argv[-2:] == ["--session-id", result.session_id]
    assert uuid.UUID(result.session_id).version == 4


def test_run_without_capture_usage_keys_no_copilot_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    captured = _patch_popen(monkeypatch)
    result = runner.run(copilot, "go", Path("/work"))
    assert result.session_id is None
    assert "--session-id" not in cast("list[str]", captured["argv"])


def test_builtin_usage_formats_pin_the_probed_capabilities() -> None:

    by_name = {s.name: s.usage_format for s in BUILTIN_RUNNERS}
    assert by_name["claude"] == CLAUDE_STREAM_JSON
    assert by_name["codex"] == CODEX_JSONL
    assert by_name["copilot"] == COPILOT_SESSION_STORE
    assert by_name[MANUAL_RUNNER] is None


def test_result_text_unwraps_each_stdout_usage_envelope() -> None:
    assert runner.result_text(_claude_json_spec(), _CLAUDE_RESULT) == "ok"
    assert runner.result_text(_claude_spec(), _CLAUDE_STREAM) == "ok"
    assert runner.result_text(_codex_spec(), _CODEX_EVENTS) == "ok"


def test_result_text_leaves_an_unwrapped_transcript_alone() -> None:

    answer = "q1: yes - ok\nq2: no - missing\n"
    assert runner.result_text(_copilot_spec(Path("store")), answer) == answer
    assert runner.result_text(RunnerSpec("x", HEADLESS, ("x",)), answer) == answer


def test_the_json_envelope_survives_a_line_the_cli_printed_around_it() -> None:

    noisy = "Warning: no stdin data received in 3s, proceeding without it.\n" + _CLAUDE_RESULT
    spec = _claude_json_spec()

    assert runner.result_text(spec, noisy) == "ok"
    usage = runner.extract_usage(spec, _executed(spec, noisy))
    assert usage is not None
    assert usage.estimated is False


def test_a_metered_dispatch_keeps_both_its_numbers_and_its_answer() -> None:

    for spec, stdout in (
        (_claude_json_spec(), _CLAUDE_RESULT),
        (_claude_spec(), _CLAUDE_STREAM),
        (_codex_spec(), _CODEX_EVENTS),
    ):
        usage = runner.extract_usage(spec, _executed(spec, stdout))
        assert usage is not None, spec.usage_format
        assert usage.estimated is False, spec.usage_format
        assert usage.tokens > 0, spec.usage_format
        assert runner.result_text(spec, stdout) == "ok", spec.usage_format


def test_only_a_family_that_reports_its_window_ships_one_as_checked() -> None:

    by_name = {s.name: (s.context_window, s.context_window_source) for s in BUILTIN_RUNNERS}
    assert by_name["claude"] == (1_000_000, runner.ADAPTER_WINDOW)
    assert by_name["codex"] == (400_000, context_window.FALLBACK_WINDOW)
    assert by_name["copilot"] == (128_000, context_window.FALLBACK_WINDOW)
    assert runner.DEFAULT_CONTEXT_WINDOW == 128_000


def test_context_occupancy_claude_json_is_unknowable() -> None:

    spec = _claude_json_spec()
    occupancy = runner.context_occupancy(spec, _executed(spec, _CLAUDE_RESULT))
    assert occupancy is None


def test_extract_usage_claude_stream_reads_the_result_event() -> None:
    spec = _claude_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_STREAM))

    assert usage is not None
    assert usage.tokens == 6 + 6000 + 15496 + 108
    assert usage.cost == pytest.approx(0.136147)
    assert usage.estimated is False


def test_context_occupancy_claude_stream_reads_the_last_assistant_turn() -> None:

    spec = _claude_spec()
    occupancy = runner.context_occupancy(spec, _executed(spec, _CLAUDE_STREAM))

    assert occupancy == 2 + 40 + 15496 + 17
    cumulative = runner.extract_usage(spec, _executed(spec, _CLAUDE_STREAM))
    assert cumulative is not None and occupancy < cumulative.tokens


def test_context_occupancy_claude_stream_ignores_noise_and_partial_lines() -> None:
    stdout = (
        "Reading prompt from stdin\n"
        '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":5}}}\n'
        '{"type":"assistant","message":{"usage":{"input_tok'
    )
    spec = _claude_spec()

    assert runner.context_occupancy(spec, _executed(spec, stdout)) == 15


def test_context_occupancy_claude_stream_is_none_without_a_turn() -> None:
    spec = _claude_spec()
    stdout = '{"type":"system","subtype":"init"}'

    assert runner.context_occupancy(spec, _executed(spec, stdout)) is None


def test_extract_usage_claude_stream_without_a_result_event_sums_its_turns() -> None:

    spec = _claude_spec()
    killed = _CLAUDE_STREAM.rsplit("\n", 1)[0] + '\n{"type":"assistant","message":{"usage'
    usage = runner.extract_usage(spec, _executed(spec, killed))

    assert usage is not None
    assert usage.estimated is False
    assert usage.tokens == 4 + 5960 + 0 + 91 + 2 + 40 + 15496 + 17
    whole = runner.extract_usage(spec, _executed(spec, _CLAUDE_STREAM))
    assert whole is not None and usage.tokens == whole.tokens
    assert usage.cost is None


def test_extract_usage_claude_stream_with_no_turn_at_all_still_estimates() -> None:
    spec = _claude_spec()
    usage = runner.extract_usage(spec, _executed(spec, '{"type":"system","subtype":"init"}'))

    assert usage is not None and usage.estimated is True


def test_context_occupancy_codex_reads_last_turn_only() -> None:

    last_turn, _total = _CODEX_TURNS[-1]
    occupancy = runner.context_occupancy(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert occupancy == last_turn["input_tokens"] + last_turn["output_tokens"]
    assert occupancy == 16829


def test_context_occupancy_never_falls_back_to_the_transcript_estimate() -> None:

    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    assert copilot.usage_format == COPILOT_SESSION_STORE
    assert runner.context_occupancy(copilot, _executed(copilot, "x" * 4000)) is None
    stdout = '{"type":"thread.started","thread_id":"t1"}\n'
    assert runner.context_occupancy(_codex_spec(), _executed(_codex_spec(), stdout)) is None


def test_context_occupancy_none_when_nothing_executed() -> None:
    handoff = RunResult(MANUAL_RUNNER, (), executed=False, handoff=True)
    assert runner.context_occupancy(RunnerSpec(MANUAL_RUNNER, HANDOFF), handoff) is None


def test_br_attribution_env_names_agent_harness_and_model() -> None:
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), model="opus")
    assert runner.br_attribution_env(spec) == {
        "BR_AGENT_NAME": "claude",
        "BR_HARNESS": "basicly-loop",
        "BR_MODEL": "opus",
    }
    unpinned = RunnerSpec("codex", HEADLESS, ("codex", PROMPT_PLACEHOLDER))
    assert "BR_MODEL" not in runner.br_attribution_env(unpinned)


def test_run_overlays_br_attribution_on_the_child_env(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_popen(monkeypatch)
    runner.run(_claude_spec(), "go", Path("/work"))
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["BR_AGENT_NAME"] == "claude"
    assert env["BR_HARNESS"] == "basicly-loop"


def test_dispatch_env_drops_an_inherited_git_dir() -> None:

    base = {"GIT_DIR": "/repo/.git/worktrees/lane", "GIT_INDEX_FILE": "/repo/.git/index"}
    env = runner.dispatch_env(_claude_spec(), base, None)

    assert "GIT_DIR" not in env
    assert "GIT_INDEX_FILE" not in env


def test_dispatch_env_drops_an_operators_forced_colour() -> None:

    base = {"FORCE_COLOR": "3", "CLICOLOR_FORCE": "1", "COLORTERM": "truecolor", "NO_COLOR": "1"}
    env = runner.dispatch_env(_claude_spec(), base, None)

    assert "FORCE_COLOR" not in env
    assert "CLICOLOR_FORCE" not in env
    assert "COLORTERM" not in env
    assert env["NO_COLOR"] == "1"


def test_dispatch_env_keeps_the_deliberate_identity_and_transport_vars() -> None:

    spec = RunnerSpec(
        "bot",
        HEADLESS,
        ("bot", "-p", PROMPT_PLACEHOLDER),
        git_name="basicly-bot",
        git_email="bot@example.com",
    )
    env = runner.dispatch_env(spec, {"GIT_DIR": "/repo/.git", "GIT_SSH_COMMAND": "ssh -i k"}, None)

    assert env["GIT_AUTHOR_NAME"] == "basicly-bot"
    assert env["GIT_COMMITTER_EMAIL"] == "bot@example.com"
    assert env["GIT_SSH_COMMAND"] == "ssh -i k"
    assert "GIT_DIR" not in env


def test_dispatch_env_drops_a_virtual_env_from_another_checkout(tmp_path: Path) -> None:

    base_tree = tmp_path / "base"
    (base_tree / "src").mkdir(parents=True)
    inherited = {"VIRTUAL_ENV": str(base_tree / ".venv"), "PATH": "/usr/bin"}

    lane = runner.dispatch_env(_claude_spec(), inherited, tmp_path / "base.worktrees" / "lane")
    same = runner.dispatch_env(_claude_spec(), inherited, base_tree / "src")

    assert "VIRTUAL_ENV" not in lane
    assert lane["PATH"] == "/usr/bin"
    assert same["VIRTUAL_ENV"] == str(base_tree / ".venv")


def test_run_does_not_hand_the_child_an_inherited_git_dir(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_popen(monkeypatch)
    monkeypatch.setenv("GIT_DIR", "/repo/.git/worktrees/lane")
    monkeypatch.setenv("EXISTING_VAR", "kept")

    runner.run(_claude_spec(), "go", Path("/work"))

    env = captured["env"]
    assert isinstance(env, dict)
    assert "GIT_DIR" not in env
    assert env["EXISTING_VAR"] == "kept"


class _HungProc:
    def __init__(self) -> None:
        self.pid = 4242
        self.returncode: int | None = None
        self.communicated = 0

    def communicate(self, timeout=None, **_kwargs):
        self.communicated += 1
        if self.communicated == 1:
            raise subprocess.TimeoutExpired(("claude",), timeout or 0)
        return "partial", ""


def test_run_timeout_returns_a_timed_out_result(monkeypatch: pytest.MonkeyPatch) -> None:
    hung = _HungProc()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_a, **_k: hung)
    killed: list[int] = []
    monkeypatch.setattr(runner, "_kill_tree", lambda proc: killed.append(proc.pid))

    result = runner.run(_claude_spec(), "go", Path("/work"), timeout=1.0)

    assert result.timed_out is True
    assert result.executed is True
    assert result.returncode is None
    assert "partial" in result.stdout
    assert killed == [hung.pid]


def test_a_posix_dispatch_starts_in_its_own_session() -> None:
    assert runner._process_isolation("posix") == (True, 0)


def test_a_windows_dispatch_starts_in_its_own_process_group() -> None:
    assert runner._process_isolation("nt") == (False, runner.CREATE_NEW_PROCESS_GROUP)


class _Stubborn:
    pid = 99

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(("agent",), timeout or 0)


SIGKILL = runner.SIGKILL


class _Polite:
    pid = 99

    def wait(self, **_kwargs):
        return -15


def _record_signals(monkeypatch: pytest.MonkeyPatch) -> list[int]:

    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid, raising=False)
    signalled: list[int] = []
    monkeypatch.setattr(
        runner.os, "killpg", lambda _pgid, signum: signalled.append(signum), raising=False
    )
    return signalled


def test_kill_tree_signals_the_group_then_hard_kills_it(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "KILL_GRACE_S", 0.01)
    signalled = _record_signals(monkeypatch)

    runner._kill_tree(cast("subprocess.Popen[str]", _Stubborn()))

    assert signalled == [signal.SIGTERM, SIGKILL]


def test_kill_tree_stops_at_the_polite_signal_when_the_group_goes_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    signalled = _record_signals(monkeypatch)

    runner._kill_tree(cast("subprocess.Popen[str]", _Polite()))

    assert signalled == [signal.SIGTERM]


def test_kill_tree_tolerates_a_dispatch_that_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.os, "name", "posix")
    monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid, raising=False)

    def gone(_pgid, _signum):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(runner.os, "killpg", gone, raising=False)

    class _Gone:
        pid = 99

        def wait(self, **_kwargs):
            raise AssertionError("must not wait on a process already gone")

    runner._kill_tree(cast("subprocess.Popen[str]", _Gone()))


def test_kill_tree_on_windows_walks_the_child_chain_with_taskkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner.os, "name", "nt")
    calls: list[list[str]] = []

    def fake_run(argv, **_kwargs):
        calls.append(list(argv))

    monkeypatch.setattr(runner.subprocess, "run", fake_run)

    class _Proc:
        pid = 777

    runner._kill_tree(cast("subprocess.Popen[str]", _Proc()))

    assert calls == [["taskkill", "/F", "/T", "/PID", "777"]]


def test_an_interrupted_dispatch_takes_its_tree_down_before_propagating(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    class _Interrupted:
        pid = 31337

        def communicate(self, **_kwargs):
            raise KeyboardInterrupt

    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_a, **_k: _Interrupted())
    killed: list[int] = []
    monkeypatch.setattr(runner, "_kill_tree", lambda proc: killed.append(proc.pid))
    monkeypatch.setattr(runner, "_drain", lambda _proc: ("", ""))

    with pytest.raises(KeyboardInterrupt):
        runner.run(_claude_spec(), "go", Path("/work"))

    assert killed == [31337]


def test_drain_gives_up_on_a_pipe_a_survivor_still_holds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(runner, "KILL_GRACE_S", 0.01)

    class _Holder:
        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(("agent",), timeout or 0)

    assert runner._drain(cast("subprocess.Popen[str]", _Holder())) == ("", "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
def test_timeout_kills_a_grandchild_the_dispatch_spawned(tmp_path: Path) -> None:

    child = (
        "import subprocess, sys, time\n"
        "kid = subprocess.Popen([sys.executable, '-c', 'import time; time.sleep(30)'])\n"
        "print(kid.pid, flush=True)\n"
        "time.sleep(30)\n"
    )
    spec = RunnerSpec("spawner", HEADLESS, (sys.executable, "-c", child, PROMPT_PLACEHOLDER))

    result = runner.run(spec, "go", tmp_path, timeout=2.0)

    assert result.timed_out is True
    grandchild = int(result.stdout.strip().splitlines()[-1])
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            os.kill(grandchild, 0)
        except OSError:
            break
        time.sleep(0.05)
    else:  # pragma: no cover - only reached on a regression
        os.kill(grandchild, SIGKILL)
        pytest.fail(f"grandchild {grandchild} survived the dispatch timeout")


def test_record_dispatch_never_raises_on_a_spec_result_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = runner.select_runner(runner.BUILTIN_RUNNERS, "manual")
    result = runner.RunResult("manual", (), executed=True, returncode=0, stdout="ok")
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-x", spec, result, prompt="p", phase="validate")

    history = runner.run_record.load_run_records(tmp_path) or {}
    (entry,) = history["basicly-x"]
    assert entry["command"] == []
    assert entry["agent"] == "manual"


def test_record_dispatch_carries_the_context_the_lane_consumed(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _claude_spec()
    result = _executed(spec, _CLAUDE_STREAM)
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(
        tmp_path,
        "basicly-fcls",
        spec,
        result,
        prompt="p",
        phase="build",
        scope_tokens=4_000,
        forecast_tokens=12_000,
    )

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-fcls"]
    assert entry["context_tokens"] == runner.context_occupancy(spec, result)
    assert entry["context_tokens"] == 2 + 40 + 15496 + 17
    assert (entry["scope_tokens"], entry["forecast_tokens"]) == (4_000, 12_000)
    assert entry["context_tokens"] < entry["tokens"]


def test_record_dispatch_records_no_context_when_the_adapter_cannot_report_one(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _claude_json_spec()
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(
        tmp_path, "basicly-fcls", spec, _executed(spec, _CLAUDE_RESULT), prompt="p", phase="build"
    )

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-fcls"]
    assert entry["context_tokens"] is None
    assert entry["tokens"] is not None


def test_record_dispatch_carries_copilot_measured_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _copilot_spec(_copilot_store(tmp_path, _COPILOT_EVENTS))
    result = _copilot_run(spec)
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-2rn9", spec, result, prompt="p", phase="build")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-2rn9"]
    assert entry["estimated"] is False
    assert entry["tokens"] == 24210 + 4
    assert (entry["input_tokens"], entry["output_tokens"]) == (24210, 4)
    assert (entry["cache_read_tokens"], entry["cache_write_tokens"]) == (0, 24208)
    assert entry["credits"] == pytest.approx(6.0564)
    assert entry["cost"] is None
    assert runner.run_record.REDACTED_PROMPT in entry["command"]
    assert "--session-id" not in entry["command"]


def test_record_dispatch_carries_the_codex_measured_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _codex_spec()
    result = _executed(spec, _codex_stream(_CODEX_TURNS[0][0]))
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-jr0l.37", spec, result, prompt="p", phase="build")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-jr0l.37"]
    assert entry["estimated"] is False
    assert entry["tokens"] == 12919
    assert (entry["input_tokens"], entry["output_tokens"]) == (12764, 155)
    assert (entry["cache_read_tokens"], entry["cache_write_tokens"]) == (9984, 0)
    assert entry["reasoning_tokens"] == 147
    assert entry["cost"] is None and entry["credits"] is None


def test_record_dispatch_records_a_missing_copilot_store_as_an_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    spec = _copilot_spec(tmp_path / "session-state")
    result = _copilot_run(spec)
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-2rn9", spec, result, prompt="p", phase="build")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-2rn9"]
    assert entry["estimated"] is True
    assert entry["tokens"] == len(result.stdout) // 4
    assert entry["credits"] is None
    assert entry["input_tokens"] is None


@pytest.fixture(autouse=True)
def _clean_process_budget():
    runner.reset_process_budget()
    yield
    runner.reset_process_budget()


def test_budget_splits_the_ceiling_into_reservation_classes() -> None:
    budget = runner.ProcessBudget(8, 3)
    assert (budget.lane_slots, budget.decider_slots, budget.helper_slots) == (3, 1, 4)
    assert budget.capacity(runner.LANE) == 3
    assert budget.capacity(runner.HELPER) == 4


@pytest.mark.parametrize(
    ("total", "concurrency"),
    [(8, 4), (8, 3), (4, 4), (2, 8), (1, 4), (0, 1), (-5, 1)],
)
def test_budget_reservations_never_overcommit_the_ceiling(total: int, concurrency: int) -> None:

    budget = runner.ProcessBudget(total, concurrency)
    assert budget.lane_slots + budget.decider_slots + budget.helper_slots <= budget.total
    assert budget.lane_slots >= 1
    assert budget.decider_slots == runner.DECIDER_SLOTS


def test_budget_keeps_the_decider_slot_when_the_ceiling_is_tight() -> None:

    budget = runner.ProcessBudget(4, 8)
    assert budget.decider_slots == 1
    assert budget.lane_slots == 3


def test_budget_helpers_queue_while_lane_and_decider_slots_stay_free() -> None:
    budget = runner.ProcessBudget(4, 2)
    started = threading.Event()
    release = threading.Event()
    second_entered = threading.Event()

    def hold_helper() -> None:
        with budget.slot(runner.HELPER):
            started.set()
            release.wait(5)

    def queued_helper() -> None:
        with budget.slot(runner.HELPER):
            second_entered.set()

    first = threading.Thread(target=hold_helper)
    first.start()
    assert started.wait(5)

    second = threading.Thread(target=queued_helper)
    second.start()
    assert not second_entered.wait(0.2)
    with budget.slot(runner.LANE), budget.slot(runner.DECIDER):
        assert budget.live(runner.LANE) == 1
        assert budget.live(runner.DECIDER) == 1

    release.set()
    assert second_entered.wait(5)
    first.join(5)
    second.join(5)
    assert budget.live(runner.HELPER) == 0


def test_budget_helper_flood_never_blocks_a_lane() -> None:
    budget = runner.ProcessBudget(6, 2)
    release = threading.Event()
    holding = threading.Semaphore(0)

    def hold_helper() -> None:
        with budget.slot(runner.HELPER):
            holding.release()
            release.wait(5)

    threads = [threading.Thread(target=hold_helper) for _ in range(budget.helper_slots)]
    for thread in threads:
        thread.start()
    for _ in threads:
        assert holding.acquire(timeout=5)

    with budget.slot(runner.LANE, timeout=1):
        assert budget.live(runner.LANE) == 1

    release.set()
    for thread in threads:
        thread.join(5)


def test_budget_releases_a_slot_when_the_dispatch_raises() -> None:
    budget = runner.ProcessBudget(8, 2)
    with pytest.raises(RuntimeError, match="boom"), budget.slot(runner.LANE):
        raise RuntimeError("boom")
    assert budget.live(runner.LANE) == 0


def test_budget_refuses_a_helper_when_no_remainder_exists() -> None:
    budget = runner.ProcessBudget(3, 2)
    assert budget.helper_slots == 0
    with (
        pytest.raises(runner.BudgetExhaustedError, match="max_agent_processes"),
        budget.slot(runner.HELPER),
    ):
        pass


def test_budget_helper_wait_times_out_rather_than_hanging_forever() -> None:
    budget = runner.ProcessBudget(4, 2)
    with (
        budget.slot(runner.HELPER),
        pytest.raises(TimeoutError, match="helper process slot"),
        budget.slot(runner.HELPER, timeout=0.05),
    ):
        pass


def test_budget_rejects_an_unknown_process_class() -> None:
    budget = runner.ProcessBudget(8, 2)
    with pytest.raises(ValueError, match="unknown process class"):
        budget.capacity("vibes")


def test_process_budget_is_configured_once_per_process() -> None:
    first = runner.configure_process_budget(8, 2)
    again = runner.configure_process_budget(64, 32)
    assert again is first
    assert first.total == 8
    assert runner.process_budget() is first


def test_process_budget_defaults_when_nothing_configured_it() -> None:
    budget = runner.process_budget()
    assert budget.total == runner.DEFAULT_MAX_AGENT_PROCESSES
    assert budget.lane_slots == budget.total // 2


def test_every_engine_dispatch_site_declares_a_class() -> None:

    src = Path(__file__).resolve().parents[1] / "src" / "basicly"
    exempt = {("cli.py", "dry_run=True"), ("cli.py", "args.prompt, cwd")}
    unbudgeted: list[str] = []
    for path in sorted(src.glob("*.py")):
        if path.name == "runner.py":
            continue
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if "runner.run(" not in line:
                continue
            if any(path.name == name and marker in line for name, marker in exempt):
                continue
            window = "\n".join(lines[max(0, number - 4) : number + 1])
            if ".slot(runner." not in window:
                unbudgeted.append(f"{path.name}:{number + 1}: {line.strip()}")
    assert not unbudgeted, "unbudgeted agent dispatch site(s): " + "; ".join(unbudgeted)


WALL_CLOCK_EXEMPT = {
    "base_lock.py": "lock staleness subtracts a filesystem mtime, not a reading of ours",
    "supervise.py": "lock staleness subtracts a filesystem mtime, not a reading of ours",
    "policy.py": "the confirm-code TTL is persisted to disk and read back by another process",
}

STAMP_COMPARISON_EXEMPT = {
    "board_sections.py": 1,
    "board_wall.py": 1,
}


def test_no_engine_interval_is_measured_on_a_wall_clock() -> None:

    src = Path(__file__).resolve().parents[1] / "src" / "basicly"
    offenders: list[str] = []
    seen_exempt: set[str] = set()
    stamped: dict[str, int] = {}
    for path in sorted(src.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if ".total_seconds()" in line:
                if path.name in STAMP_COMPARISON_EXEMPT:
                    stamped[path.name] = stamped.get(path.name, 0) + 1
                else:
                    offenders.append(
                        f"{path.name}:{number + 1}: duration from a datetime difference"
                    )
                continue
            if "time.time()" not in line:
                continue
            if path.name not in WALL_CLOCK_EXEMPT:
                offenders.append(f"{path.name}:{number + 1}: {line.strip()}")
                continue
            window = "\n".join(lines[max(0, number - 3) : number + 1])
            if "def _now(" not in window:
                offenders.append(f"{path.name}:{number + 1}: outside the exempt _now() seam")
            else:
                seen_exempt.add(path.name)
    assert not offenders, "wall-clock interval(s) in the engine: " + "; ".join(offenders)
    assert stamped == STAMP_COMPARISON_EXEMPT, (
        f"stamp-comparison exemptions moved: recorded {STAMP_COMPARISON_EXEMPT}, found {stamped}"
    )
    assert seen_exempt == set(WALL_CLOCK_EXEMPT), (
        f"stale wall-clock exemption(s): {sorted(set(WALL_CLOCK_EXEMPT) - seen_exempt)}"
    )


def _wait_until(predicate: Callable[[], bool], *, timeout: float) -> None:

    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)


def test_stall_watchdog_flags_an_unchanging_dispatch_exactly_once() -> None:
    fired: list[float] = []
    with runner.StallWatchdog(
        0.05, probe=lambda: "frozen", on_stall=lambda: fired.append(time.monotonic()), poll=0.01
    ):
        _wait_until(lambda: len(fired) == 1, timeout=10)
        time.sleep(0.3)
    assert len(fired) == 1


def test_stall_watchdog_stays_quiet_while_the_lane_makes_progress() -> None:
    counter = itertools.count()
    fired: list[int] = []
    with runner.StallWatchdog(
        0.5, probe=lambda: str(next(counter)), on_stall=lambda: fired.append(1), poll=0.01
    ):
        time.sleep(0.3)
    assert fired == []


def test_stall_watchdog_flags_a_lane_that_goes_quiet_after_working() -> None:
    moving = {"value": "a", "frozen": False}
    fired: list[int] = []

    def probe() -> str:
        if moving["frozen"]:
            return moving["value"]
        moving["value"] += "a"
        return moving["value"]

    with runner.StallWatchdog(
        0.5, probe=probe, on_stall=lambda: fired.append(1), poll=0.01
    ) as watchdog:
        time.sleep(0.2)
        assert fired == [], "working lane flagged"
        moving["frozen"] = True
        _wait_until(lambda: watchdog.flagged, timeout=10)
    assert fired == [1]


def test_stall_watchdog_never_lets_a_failing_probe_or_notifier_escape() -> None:

    def exploding_probe() -> str:
        raise OSError("worktree vanished")

    def exploding_notifier() -> None:
        raise RuntimeError("tracker down")

    with runner.StallWatchdog(
        0.05, probe=exploding_probe, on_stall=exploding_notifier, poll=0.01
    ) as watchdog:
        _wait_until(lambda: watchdog.flagged, timeout=10)
    assert watchdog.flagged is True


def test_stall_watchdog_stops_cleanly_before_it_ever_fires() -> None:
    fired: list[int] = []
    watchdog = runner.StallWatchdog(
        60.0, probe=lambda: "x", on_stall=lambda: fired.append(1), poll=0.02
    )
    with watchdog:
        time.sleep(0.05)
    assert fired == []
    assert watchdog.flagged is False


_CLAUDE_MODEL_STREAM = (
    '{"type":"system","subtype":"init","model":"claude-haiku-4-5-20251001"}\n'
    '{"type":"assistant","message":{"model":"claude-haiku-4-5-20251001","usage":'
    '{"input_tokens":10,"output_tokens":44}}}\n'
    '{"type":"result","total_cost_usd":0.048,"usage":{"input_tokens":10,"output_tokens":44},'
    '"modelUsage":{"claude-haiku-4-5-20251001":{"inputTokens":10,"outputTokens":44,'
    '"canonicalModel":"claude-haiku-4-5"}}}\n'
)


def _tier_map(model: str = "claude-haiku-4-5", *, status: str = "available") -> dict:
    cell: dict[str, object] = {"status": status}
    if status == "available":
        cell["model"] = model
    else:
        cell["reason"] = "the fixture marks this cell unavailable"
    return {"tiers": {"low": {"vendors": {"anthropic": {"surfaces": {"anthropic": cell}}}}}}


def test_a_resolvable_tier_pins_the_surface_spelling() -> None:
    spec = replace(runner.select_runner(runner.BUILTIN_RUNNERS, "claude"), tier="low")
    resolution = runner.resolve_model(spec, mapping=_tier_map())

    assert resolution.model == "claude-haiku-4-5"
    assert resolution.tier == "low"
    assert resolution.source == "agent tier"
    assert resolution.honoured is True


def test_an_unresolvable_tier_refuses_and_names_the_agent_and_the_config_key() -> None:

    spec = replace(runner.select_runner(runner.BUILTIN_RUNNERS, "claude"), tier="low")
    with pytest.raises(models.ModelResolutionError) as excinfo:
        runner.resolve_model(spec, mapping=_tier_map(status="unavailable"))

    message = str(excinfo.value)
    assert "'claude'" in message
    assert "tier" in message
    assert "unavailable" in message


def test_an_unresolvable_tier_starts_no_agent_process(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    def forbidden(*_args: object, **_kwargs: object) -> object:
        raise AssertionError("run() spawned a process for an unresolvable tier")

    monkeypatch.setattr(runner.subprocess, "Popen", forbidden)
    spec = replace(
        runner.select_runner(runner.BUILTIN_RUNNERS, "claude"),
        tier="maximum",
        vendor="moonshotai",
    )

    with pytest.raises(models.ModelResolutionError):
        runner.run(spec, "go", tmp_path)


def test_an_explicit_model_pin_wins_over_a_tier() -> None:
    spec = replace(runner.select_runner(runner.BUILTIN_RUNNERS, "claude"), tier="low", model="opus")
    resolution = runner.resolve_model(spec, mapping=_tier_map())

    assert resolution.model == "opus"
    assert resolution.source == runner.AGENT_MODEL_PIN


def test_a_defaulted_tier_records_that_it_came_from_the_family_default() -> None:

    spec = replace(
        runner.select_runner(runner.BUILTIN_RUNNERS, "claude"),
        tier="low",
        tier_source=runner.FAMILY_DEFAULT_TIER,
    )
    resolution = runner.resolve_model(spec, mapping=_tier_map())

    assert resolution.model == "claude-haiku-4-5"
    assert resolution.source == runner.FAMILY_DEFAULT_TIER


def test_a_refusal_names_default_tier_when_the_tier_was_defaulted() -> None:
    spec = replace(
        runner.select_runner(runner.BUILTIN_RUNNERS, "claude"),
        tier="low",
        tier_source=runner.FAMILY_DEFAULT_TIER,
    )
    with pytest.raises(models.ModelResolutionError, match="default_tier"):
        runner.resolve_model(spec, mapping=_tier_map(status="unavailable"))


def test_no_tier_and_no_model_leaves_the_dispatch_unpinned(tmp_path: Path) -> None:
    spec = runner.select_runner(runner.BUILTIN_RUNNERS, "claude")
    result = runner.run(spec, "go", tmp_path, dry_run=True)

    assert result.model_resolution is None
    assert "--model" not in result.command


def test_a_family_that_cannot_express_a_tier_records_the_fallback(tmp_path: Path) -> None:

    spec = replace(runner.select_runner(runner.BUILTIN_RUNNERS, "manual"), tier="low")
    result = runner.run(spec, "go", tmp_path)

    assert result.model_resolution is not None
    assert result.model_resolution.honoured is False
    assert result.model_resolution.tier == "low"
    assert result.model_resolution.model is None
    assert "not applied" in (result.model_resolution.note or "")


def test_the_observed_model_comes_off_a_real_claude_envelope() -> None:
    spec = runner.select_runner(runner.BUILTIN_RUNNERS, "claude")
    result = runner.RunResult(
        "claude", ("claude",), executed=True, returncode=0, stdout=_CLAUDE_MODEL_STREAM
    )
    assert runner.observed_models(spec, result) == ("claude-haiku-4-5",)


def test_a_dated_build_of_the_pinned_model_is_not_a_mismatch() -> None:
    seen = ("claude-haiku-4-5",)
    assert runner.model_mismatch("claude-haiku-4-5", seen) is None
    assert runner.model_mismatch("haiku", seen) is None


def test_a_different_observed_model_is_recorded_as_a_mismatch() -> None:
    mismatch = runner.model_mismatch("claude-opus-5", ("claude-haiku-4-5",))
    assert mismatch is not None
    assert "claude-opus-5" in mismatch
    assert "claude-haiku-4-5" in mismatch


def test_codex_reports_no_model_so_nothing_is_observed_or_claimed() -> None:

    spec = runner.select_runner(runner.BUILTIN_RUNNERS, "codex")
    stdout = (
        '{"type":"thread.started","thread_id":"t"}\n'
        '{"type":"turn.completed","usage":{"input_tokens":13239,"output_tokens":5}}\n'
    )
    result = runner.RunResult("codex", ("codex",), executed=True, returncode=0, stdout=stdout)

    assert runner.observed_models(spec, result) == ()
    assert runner.model_mismatch("gpt-5.6-terra", ()) is None


def test_a_copilot_dispatch_that_switched_model_reports_both(tmp_path: Path) -> None:

    session = "11111111-2222-3333-4444-555555555555"
    store = tmp_path / session
    store.mkdir()
    (store / copilot_store.COPILOT_EVENTS_FILE).write_text(
        json.dumps({
            "type": "session.shutdown",
            "data": {
                "modelMetrics": {
                    "claude-opus-5": {"usage": {"inputTokens": 10, "outputTokens": 2}},
                    "claude-haiku-4.5": {"usage": {"inputTokens": 3, "outputTokens": 1}},
                }
            },
        })
        + "\n",
        encoding="utf-8",
    )
    spec = replace(runner.select_runner(runner.BUILTIN_RUNNERS, "copilot"), session_store=tmp_path)
    result = runner.RunResult(
        "copilot", ("copilot",), executed=True, returncode=0, session_id=session
    )

    assert runner.observed_models(spec, result) == ("claude-opus-5", "claude-haiku-4.5")
    assert runner.model_mismatch("claude-haiku-4-5", runner.observed_models(spec, result)) is None


def test_record_dispatch_writes_the_model_provenance(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)
    spec = replace(runner.select_runner(runner.BUILTIN_RUNNERS, "claude"), tier="low")
    result = runner.RunResult(
        "claude",
        ("claude",),
        executed=True,
        returncode=0,
        stdout=_CLAUDE_MODEL_STREAM,
        model_resolution=models.ModelResolution(
            model="claude-haiku-4-5", tier="low", source="agent tier"
        ),
    )

    runner.record_dispatch(tmp_path, "basicly-t", spec, result, prompt="p", phase="lane")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-t"]
    assert entry["model"] == "claude-haiku-4-5"
    assert entry["model_tier"] == "low"
    assert entry["model_source"] == "agent tier"
    assert entry["tier_honoured"] is True
    assert entry["observed_models"] == ["claude-haiku-4-5"]
    assert entry["model_mismatch"] is None


def test_record_dispatch_records_a_model_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)
    spec = runner.select_runner(runner.BUILTIN_RUNNERS, "claude")
    result = runner.RunResult(
        "claude",
        ("claude",),
        executed=True,
        returncode=0,
        stdout=_CLAUDE_MODEL_STREAM,
        model_resolution=models.ModelResolution(
            model="claude-opus-5", tier="maximum", source="agent tier"
        ),
    )

    runner.record_dispatch(tmp_path, "basicly-t", spec, result, prompt="p", phase="lane")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-t"]
    assert entry["model_mismatch"] is not None
    assert "claude-opus-5" in entry["model_mismatch"]


_RECORDED_OCCUPANCY = {
    "basicly-tcmy.5": 223_221,
    "basicly-gczc": 210_721,
    "basicly-vkh0.10": 193_096,
    "basicly-tcmy.6": 190_177,
    "basicly-tcmy.22": 171_029,
    "basicly-jr0l.64": 170_530,
    "basicly-8ry8": 80_211,
}

_STALE_CLAUDE_WINDOW = 200_000


def _ledger(occupancy: dict[str, int], agent: str = "claude") -> dict[str, list]:
    return {
        bead: [{"agent": agent, "phase": "lane", "context_tokens": tokens}]
        for bead, tokens in occupancy.items()
    }


def _repo_specs() -> dict[str, RunnerSpec]:
    return {spec.name: spec for spec in load_runner_config(REPO_ROOT).specs}


def test_no_recorded_occupancy_exceeds_its_runners_declared_window() -> None:

    assert runner.window_violations(run_record.dispatch_history(REPO_ROOT), _repo_specs()) == []


def test_the_ledger_holds_occupancy_the_stale_declaration_called_impossible() -> None:

    measured = [
        tokens
        for entries in run_record.dispatch_history(REPO_ROOT).values()
        for entry in entries
        if isinstance(tokens := entry.get("context_tokens"), int)
    ]
    assert measured, "no dispatch carries a measured occupancy — the gate would be inert"
    assert max(measured) > _STALE_CLAUDE_WINDOW


def test_the_window_gate_names_both_figures_when_a_lane_outgrows_the_declaration() -> None:

    stale = replace(
        _claude_spec(),
        context_window=_STALE_CLAUDE_WINDOW,
        context_window_source=runner.ADAPTER_WINDOW,
    )

    violations = runner.window_violations(_ledger(_RECORDED_OCCUPANCY), {"claude": stale})

    assert len(violations) == 2
    report = "\n".join(violations)
    assert "223,221" in report and "210,721" in report
    assert "200,000" in report
    assert runner.ADAPTER_WINDOW in report
    assert "193,096" not in report and "80,211" not in report


def test_an_occupancy_inside_the_declared_window_is_not_a_violation() -> None:

    declared = _repo_specs()["claude"]
    assert max(_RECORDED_OCCUPANCY.values()) < declared.context_window

    assert runner.window_violations(_ledger(_RECORDED_OCCUPANCY), {"claude": declared}) == []


def test_a_record_whose_agent_has_no_spec_cannot_be_a_violation() -> None:
    ledger = _ledger({"basicly-x": 10_000_000}, agent="an-agent-nothing-declares")
    assert runner.window_violations(ledger, _repo_specs()) == []


def test_the_repo_declares_its_context_window_rather_than_inheriting_a_default() -> None:

    claude = _repo_specs()["claude"]
    assert claude.context_window_source == context_window.DECLARED_WINDOW
    assert claude.context_window != runner.DEFAULT_CONTEXT_WINDOW
    assert _repo_specs()["codex"].context_window_source == context_window.FALLBACK_WINDOW


def test_record_dispatch_carries_the_window_the_occupancy_was_measured_against(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = replace(
        _claude_spec(),
        context_window=1_000_000,
        context_window_source=context_window.DECLARED_WINDOW,
    )
    result = _executed(spec, _CLAUDE_STREAM)
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-23ep", spec, result, prompt="p", phase="lane")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-23ep"]
    assert entry["context_window"] == 1_000_000
    assert entry["context_window_source"] == context_window.DECLARED_WINDOW
    assert entry["context_tokens"] == runner.context_occupancy(spec, result)
    assert entry["context_tokens"] < entry["context_window"]


def _emitter(body: str) -> str:
    return "import json, os, sys, time\n" + body


def _streaming_spec(body: str, *, usage_format: str = CLAUDE_STREAM_JSON) -> RunnerSpec:
    return RunnerSpec(
        "claude" if usage_format == CLAUDE_STREAM_JSON else "codex",
        HEADLESS,
        (sys.executable, "-c", _emitter(body), PROMPT_PLACEHOLDER),
        usage_format=usage_format,
    )


def test_event_usage_sums_to_the_terminal_total_on_both_streaming_adapters() -> None:

    for spec, stream in ((_claude_spec(), _CLAUDE_STREAM), (_codex_spec(), _CODEX_EVENTS)):
        live = sum(
            usage.tokens
            for event in runner_envelope.stream_events(stream)
            if (usage := runner.event_usage(spec, event)) is not None
        )
        terminal = runner.extract_usage(spec, _executed(spec, stream))
        assert terminal is not None
        assert live == terminal.tokens, spec.name


def test_event_usage_is_none_for_an_adapter_with_no_stream_format() -> None:
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    turn = {"type": "assistant", "message": {"usage": {"input_tokens": 5}}}
    assert runner.event_usage(copilot, turn) is None
    assert runner.event_usage(_claude_spec(), {"type": "system", "subtype": "init"}) is None
    assert runner.event_usage(_codex_spec(), {"type": "turn.completed"}) is None


def test_run_observes_each_event_while_the_dispatch_is_still_running(tmp_path: Path) -> None:

    ack = tmp_path / "ack"
    body = (
        "ack = sys.argv[1]\n"
        "turn = {'type':'assistant','message':{'usage':{'input_tokens':7,'output_tokens':3}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        "deadline = time.monotonic() + 30\n"
        "while not os.path.exists(ack):\n"
        "    if time.monotonic() >= deadline:\n"
        "        sys.exit(9)\n"
        "    time.sleep(0.01)\n"
        "res = {'type':'result','result':'ok','usage':{'input_tokens':7,'output_tokens':3}}\n"
        "sys.stdout.write(json.dumps(res) + '\\n')\n"
    )
    spec = RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", _emitter(body), str(ack), PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )
    seen: list[runner.StreamEvent] = []

    def sink(event: runner.StreamEvent) -> None:
        seen.append(event)
        ack.touch()

    result = runner.run(spec, "go", tmp_path, capture_usage=True, on_event=sink, timeout=60.0)

    assert result.returncode == 0
    assert [event.data["type"] for event in seen if event.data] == ["assistant", "result"]
    assert seen[0].usage is not None and seen[0].usage.tokens == 10


def test_streaming_leaves_the_captured_output_and_the_totals_identical(tmp_path: Path) -> None:

    body = (
        "for line in json.loads(sys.argv[1]):\n"
        "    sys.stdout.write(line + '\\n'); sys.stdout.flush()\n"
        "sys.stderr.write('a warning\\n')\n"
    )
    spec = RunnerSpec(
        "claude",
        HEADLESS,
        (
            sys.executable,
            "-c",
            _emitter(body),
            json.dumps(_CLAUDE_STREAM.splitlines()),
            PROMPT_PLACEHOLDER,
        ),
        usage_format=CLAUDE_STREAM_JSON,
    )

    batched = runner.run(spec, "go", tmp_path, capture_usage=True, timeout=60.0)
    streamed = runner.run(
        spec, "go", tmp_path, capture_usage=True, on_event=lambda _e: None, timeout=60.0
    )

    assert streamed.stdout == batched.stdout
    assert streamed.stderr == batched.stderr
    assert streamed.returncode == batched.returncode == 0
    assert runner.extract_usage(spec, streamed) == runner.extract_usage(spec, batched)
    assert runner.result_text(spec, streamed.stdout) == runner.result_text(spec, batched.stdout)


def test_stream_reader_replaces_undecodable_bytes_and_keeps_reading(tmp_path: Path) -> None:

    body = (
        "turn = {'type':'assistant','message':{'usage':{'input_tokens':4,'output_tokens':1}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        "sys.stdout.buffer.write(b'progress \\x81 note\\n'); sys.stdout.buffer.flush()\n"
        "res = {'type':'result','result':'ok','usage':{'input_tokens':4,'output_tokens':1}}\n"
        "sys.stdout.write(json.dumps(res) + '\\n')\n"
    )
    spec = _streaming_spec(body)
    seen: list[runner.StreamEvent] = []

    result = runner.run(
        spec, "go", tmp_path, capture_usage=True, on_event=seen.append, timeout=60.0
    )

    assert result.returncode == 0
    assert "\ufffd" in result.stdout
    assert [event.data["type"] for event in seen if event.data] == ["assistant", "result"]
    usage = runner.extract_usage(spec, result)
    assert usage is not None and usage.estimated is False


def test_streaming_drains_stderr_so_neither_pipe_can_fill(tmp_path: Path) -> None:

    noise = 200_000
    body = (
        "sys.stderr.write('x' * int(sys.argv[1])); sys.stderr.flush()\n"
        "res = {'type':'result','result':'ok','usage':{'input_tokens':1,'output_tokens':1}}\n"
        "sys.stdout.write(json.dumps(res) + '\\n')\n"
    )
    spec = RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", _emitter(body), str(noise), PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )

    result = runner.run(
        spec, "go", tmp_path, capture_usage=True, on_event=lambda _e: None, timeout=60.0
    )

    assert result.timed_out is False
    assert result.returncode == 0
    assert len(result.stderr) == noise


def test_streaming_timeout_keeps_the_partial_transcript(tmp_path: Path) -> None:

    body = (
        "turn = {'type':'assistant','message':{'usage':{'input_tokens':7,'output_tokens':3}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        "time.sleep(60)\n"
    )
    seen: list[runner.StreamEvent] = []

    result = runner.run(
        _streaming_spec(body), "go", tmp_path, capture_usage=True, on_event=seen.append, timeout=1.0
    )

    assert result.timed_out is True
    assert result.returncode is None
    assert '"type": "assistant"' in result.stdout
    assert len(seen) == 1


def test_streamed_events_are_redacted_before_the_sink_sees_them(tmp_path: Path) -> None:

    token = "ghp_" + "b" * 30
    body = (
        "turn = {'type':'assistant','text':'pushed with ' + sys.argv[1],\n"
        "        'message':{'usage':{'input_tokens':4,'output_tokens':1}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n')\n"
    )
    spec = RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", _emitter(body), token, PROMPT_PLACEHOLDER),
        usage_format=CLAUDE_STREAM_JSON,
    )
    seen: list[runner.StreamEvent] = []

    result = runner.run(
        spec, "go", tmp_path, capture_usage=True, on_event=seen.append, timeout=60.0
    )

    (event,) = seen
    assert token not in event.line and "<redacted:github-token>" in event.line
    assert event.data is not None and token not in json.dumps(event.data)
    assert event.usage is not None and event.usage.tokens == 5
    assert token not in result.stdout


def test_a_sink_that_raises_never_stops_the_stream(tmp_path: Path) -> None:

    body = (
        "for turn in (1, 2, 3):\n"
        "    sys.stdout.write(json.dumps({'type':'assistant','n':turn,\n"
        "        'message':{'usage':{'input_tokens':turn,'output_tokens':0}}}) + '\\n')\n"
    )
    seen: list[int] = []

    def angry(event: runner.StreamEvent) -> None:
        if event.data is not None:
            seen.append(event.data["n"])
        raise RuntimeError("sink is broken")

    result = runner.run(
        _streaming_spec(body), "go", tmp_path, capture_usage=True, on_event=angry, timeout=60.0
    )

    assert seen == [1, 2, 3]
    assert result.returncode == 0
    assert result.stdout.count("assistant") == 3


def test_a_sink_is_inert_for_an_adapter_that_measures_out_of_band(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    captured = _patch_popen(monkeypatch, stdout="plain text answer")
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    seen: list[runner.StreamEvent] = []

    result = runner.run(
        copilot, "go", Path("/work"), capture_usage=True, on_event=seen.append, timeout=60.0
    )

    assert seen == []
    assert result.stdout == "plain text answer"
    assert captured["errors"] is None


def test_no_sink_keeps_a_streaming_adapter_on_the_single_read(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    captured = _patch_popen(monkeypatch, stdout=_CLAUDE_STREAM)

    result = runner.run(_claude_spec(), "go", Path("/work"), capture_usage=True)

    assert captured["errors"] is None
    assert result.stdout == _CLAUDE_STREAM


def test_an_unmetered_dispatch_streams_nothing_even_with_a_sink(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    captured = _patch_popen(monkeypatch, stdout="plain")
    seen: list[runner.StreamEvent] = []

    runner.run(_claude_spec(), "go", Path("/work"), on_event=seen.append)

    assert seen == []
    assert captured["errors"] is None


def test_streaming_sends_a_stdin_prompt_without_deadlocking(tmp_path: Path) -> None:

    body = (
        "prompt = sys.stdin.read()\n"
        "res = {'type':'result','result':str(len(prompt)),\n"
        "       'usage':{'input_tokens':1,'output_tokens':1}}\n"
        "sys.stdout.write(json.dumps(res) + '\\n')\n"
    )
    spec = RunnerSpec(
        "claude",
        HEADLESS,
        (sys.executable, "-c", _emitter(body)),
        prompt_via="stdin",
        usage_format=CLAUDE_STREAM_JSON,
    )
    prompt = "y" * 200_000

    result = runner.run(
        spec, prompt, tmp_path, capture_usage=True, on_event=lambda _e: None, timeout=60.0
    )

    assert result.timed_out is False
    assert runner.result_text(spec, result.stdout) == str(len(prompt))


def _spending_child(sleep_s: float = 60.0) -> str:
    return (
        "turn = {'type':'assistant','message':"
        "{'usage':{'input_tokens':7,'output_tokens':3}}}\n"
        "sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        f"time.sleep({sleep_s})\n"
    )


def test_a_dispatch_is_stopped_on_the_spend_bound_not_on_its_wall_clock(tmp_path: Path) -> None:

    seen: list[runner.StreamEvent] = []

    result = runner.run(
        _streaming_spec(_spending_child()),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=seen.append,
        timeout=60.0,
        bounds=runner.DispatchBounds(token_ceiling=10),
    )

    assert result.stopped == runner.StopReason(
        runner.SPEND_BOUND, "10 tokens reported against a 10 lane ceiling"
    )
    assert result.timed_out is True
    assert result.returncode is None
    assert '"input_tokens": 7' in result.stdout


def test_a_dispatch_with_a_silent_stream_is_stopped_on_the_quiet_bound(tmp_path: Path) -> None:

    result = runner.run(
        _streaming_spec("time.sleep(60)\n"),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=lambda _e: None,
        timeout=60.0,
        bounds=runner.DispatchBounds(quiet_after=0.3),
    )

    assert result.stopped is not None
    assert result.stopped.bound == runner.QUIET_BOUND
    assert "no stream events for 0.3s" in result.stopped.detail
    assert result.timed_out is True


def test_a_lane_emitting_events_inside_its_budget_outlives_the_bound(tmp_path: Path) -> None:

    body = (
        "turn = {'type':'assistant','message':{'usage':{'input_tokens':1,'output_tokens':0}}}\n"
        "for _ in range(10):\n"
        "    sys.stdout.write(json.dumps(turn) + '\\n'); sys.stdout.flush()\n"
        "    time.sleep(0.1)\n"
        "res = {'type':'result','result':'ok','usage':{'input_tokens':1,'output_tokens':0}}\n"
        "sys.stdout.write(json.dumps(res) + '\\n')\n"
    )
    seen: list[runner.StreamEvent] = []

    result = runner.run(
        _streaming_spec(body),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=seen.append,
        timeout=60.0,
        bounds=runner.DispatchBounds(quiet_after=0.3, token_ceiling=10_000_000),
    )

    assert result.returncode == 0
    assert result.stopped is None
    assert result.timed_out is False
    assert len(seen) == 11


def test_the_wall_clock_stays_terminal_underneath_both_bounds(tmp_path: Path) -> None:

    result = runner.run(
        _streaming_spec("time.sleep(60)\n"),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=lambda _e: None,
        timeout=0.3,
        bounds=runner.DispatchBounds(token_ceiling=10_000_000),
    )

    assert result.timed_out is True
    assert result.stopped is None
    assert runner.stop_label(result, 0.3) == "runner_timeout after 0s"


def test_stop_label_names_the_bound_every_surface_reports_it_by() -> None:
    killed = runner.RunResult("claude", (), executed=True, timed_out=True)
    assert runner.stop_label(killed, 3600.0) == "runner_timeout after 3600s"

    bounded = replace(killed, stopped=runner.StopReason(runner.SPEND_BOUND, "0 left"))
    assert runner.stop_label(bounded, 3600.0) == "spend bound: 0 left"


def test_an_unbounded_dispatch_still_takes_its_wall_clock_in_one_wait(tmp_path: Path) -> None:

    inert = runner.DispatchBounds()
    assert inert.armed is False

    result = runner.run(
        _streaming_spec("time.sleep(60)\n"),
        "go",
        tmp_path,
        capture_usage=True,
        on_event=lambda _e: None,
        timeout=0.3,
        bounds=inert,
    )

    assert result.timed_out is True
    assert result.stopped is None


def test_the_bound_interval_samples_several_times_per_quiet_window() -> None:
    assert runner.DispatchBounds().interval() == runner.STOP_POLL_S
    assert runner.DispatchBounds(quiet_after=1800.0).interval() == runner.STOP_POLL_S
    assert runner.DispatchBounds(quiet_after=0.4).interval() == 0.1


def test_the_quiet_bound_default_leaves_room_for_the_longest_real_tool_call(
    tmp_path: Path,
) -> None:

    engine = load_runner_config(tmp_path)
    assert engine.stall_after < engine.quiet_after < engine.runner_timeout
    assert engine.quiet_after >= 10 * 76

    declared = load_runner_config(REPO_ROOT)
    assert declared.stall_after < declared.quiet_after < declared.runner_timeout
    assert declared.runner_timeout > 1712


def test_a_record_names_the_role_and_model_a_lane_dispatch_carried(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _claude_spec()
    argv = runner.format_command(spec, _PROMPT, capture_usage=True, role="implementer")
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(
        tmp_path,
        "basicly-jn1x",
        spec,
        RunResult(spec.name, tuple(argv), executed=True, returncode=0, stdout=_CLAUDE_RESULT),
        prompt=_PROMPT,
        phase="lane",
    )

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-jn1x"]
    assert "--agent" in entry["command"]
    assert "implementer" in entry["command"]
    assert _PROMPT not in entry["command"]
    assert runner.run_record.REDACTED_PROMPT in entry["command"]


def test_a_record_omits_the_usage_flags_a_dispatch_did_not_carry(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _claude_spec()
    argv = runner.format_command(spec, _PROMPT)
    assert "--output-format" in runner.format_command(spec, _PROMPT, capture_usage=True)
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(
        tmp_path,
        "basicly-tcmy.33",
        spec,
        RunResult(spec.name, tuple(argv), executed=True, returncode=0, stdout="plain"),
        prompt=_PROMPT,
        phase="decide",
    )

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-tcmy.33"]
    assert "--output-format" not in entry["command"]


def test_an_unknown_prompt_records_no_argv_at_all(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:

    spec = _claude_spec()
    argv = runner.format_command(spec, "a secret prompt")
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(
        tmp_path,
        "basicly-jn1x.2",
        spec,
        RunResult(spec.name, tuple(argv), executed=True, returncode=0, stdout="plain"),
        phase="lane",
    )

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-jn1x.2"]
    assert entry["command"] == []

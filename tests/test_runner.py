"""Tests for the agent-agnostic runner adapters (onb.7).

A runner only invokes an agent headless: it formats an exact argv (or hands off),
detects which agent to use, and captures output. These tests pin that behavior
and — crucially — that an unknown agent's command line is never guessed: `auto`
falls back to the manual handoff runner, which never shells out.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import time
from pathlib import Path
from typing import cast

import pytest

from basicly import runner
from basicly.runner import (
    BUILTIN_RUNNERS,
    CLAUDE_JSON,
    CLAUDE_STREAM_JSON,
    CODEX_JSONL,
    HANDOFF,
    HEADLESS,
    MANUAL_RUNNER,
    PROMPT_PLACEHOLDER,
    RunnerSpec,
    RunResult,
)


def _which_none(_binary: str) -> str | None:
    return None


def _which_only(*available: str):
    def which(binary: str) -> str | None:
        return f"/usr/bin/{binary}" if binary in available else None

    return which


# --- format_command ---------------------------------------------------------


def test_format_command_injects_prompt_as_arg() -> None:
    """An arg-injected template replaces the single placeholder with the prompt."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do the thing") == ["claude", "-p", "do the thing"]


def test_format_command_stdin_keeps_prompt_out_of_argv() -> None:
    """A stdin runner's argv never carries the prompt (it goes on stdin at run time)."""
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin")
    assert runner.format_command(spec, "prompt text") == ["x", "--headless"]


def test_format_command_rejects_handoff() -> None:
    """A handoff runner has no command line."""
    with pytest.raises(ValueError, match="not headless"):
        runner.format_command(RunnerSpec(MANUAL_RUNNER, HANDOFF), "p")


def test_format_command_rejects_arg_template_without_placeholder() -> None:
    """An arg-injected template missing the placeholder would silently drop the prompt."""
    spec = RunnerSpec("bad", HEADLESS, ("bad", "run"))
    with pytest.raises(ValueError, match="placeholder"):
        runner.format_command(spec, "p")


# --- format_command: model pinning (basicly-45ld) ---------------------------


def test_format_command_no_model_leaves_argv_unchanged() -> None:
    """The default (no model) never touches the argv."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do it") == ["claude", "-p", "do it"]


def test_format_command_injects_model_after_binary() -> None:
    """A pinned model with no placeholder injects `--model <value>` right after the binary."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), model="opus")
    assert runner.format_command(spec, "do it") == ["claude", "--model", "opus", "-p", "do it"]


def test_format_command_substitutes_model_placeholder() -> None:
    """A `{model}` placeholder is the escape hatch for a non-`--model` flag: substitute it."""
    spec = RunnerSpec(
        "acme",
        HEADLESS,
        ("acme", "--llm", runner.MODEL_PLACEHOLDER, "run", PROMPT_PLACEHOLDER),
        model="fast-1",
    )
    assert runner.format_command(spec, "go") == ["acme", "--llm", "fast-1", "run", "go"]


def test_format_command_model_placeholder_without_model_raises() -> None:
    """A `{model}` slot with no model to fill it is a config error, not a literal in argv."""
    spec = RunnerSpec(
        "acme", HEADLESS, ("acme", "--llm", runner.MODEL_PLACEHOLDER, PROMPT_PLACEHOLDER)
    )
    with pytest.raises(ValueError, match="no model is set"):
        runner.format_command(spec, "go")


def test_format_command_injects_model_for_stdin_runner() -> None:
    """Model injection applies regardless of how the prompt is delivered."""
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin", model="m1")
    assert runner.format_command(spec, "ignored") == ["x", "--model", "m1", "--headless"]


# --- format_command: deny-tool injection (basicly-lqz5) ---------------------


def test_format_command_no_deny_tools_leaves_argv_unchanged() -> None:
    """The default (no deny_tools) never touches the argv."""
    spec = RunnerSpec("copilot", HEADLESS, ("copilot", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do it") == ["copilot", "-p", "do it"]


def test_format_command_injects_deny_tool_flags_after_binary() -> None:
    """Each deny-tool spec becomes one `--deny-tool=<spec>` argv token after the binary."""
    spec = RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        deny_tools=("shell(rm -rf)", "shell(git push --force)"),
    )
    assert runner.format_command(spec, "go") == [
        "copilot",
        "--deny-tool=shell(rm -rf)",
        "--deny-tool=shell(git push --force)",
        "-p",
        "go",
    ]


def test_format_command_deny_tools_compose_after_model() -> None:
    """Model injection then deny-tool injection both land after the binary, model first."""
    spec = RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        model="fast",
        deny_tools=("write",),
    )
    assert runner.format_command(spec, "go") == [
        "copilot",
        "--model",
        "fast",
        "--deny-tool=write",
        "-p",
        "go",
    ]


# --- format_command: sandbox/approval guardrails (basicly-t0kt) -------------


def test_format_command_no_sandbox_or_approval_leaves_argv_unchanged() -> None:
    """The default (neither set) never touches the argv."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.format_command(spec, "do it") == ["claude", "-p", "do it"]


def test_format_command_injects_sandbox_and_approval_after_binary() -> None:
    """Sandbox then approval flags land after the binary when both are set."""
    spec = RunnerSpec(
        "codex",
        HEADLESS,
        ("codex", "exec", PROMPT_PLACEHOLDER),
        sandbox="workspace-write",
        approval="on-failure",
    )
    assert runner.format_command(spec, "go") == [
        "codex",
        "--sandbox",
        "workspace-write",
        "-a",
        "on-failure",
        "exec",
        "go",
    ]


def test_format_command_injects_sandbox_alone() -> None:
    """Approval unset injects only the sandbox flag."""
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER), sandbox="read-only")
    assert runner.format_command(spec, "go") == ["codex", "--sandbox", "read-only", "exec", "go"]


def test_codex_builtin_defaults_render_workspace_write_on_failure() -> None:
    """The shipped codex adapter carries the guardrail defaults into its rendered argv."""
    codex = next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")
    assert codex.sandbox == "workspace-write"
    assert codex.approval == "on-failure"
    assert runner.format_command(codex, "do the work") == [
        "codex",
        "--sandbox",
        "workspace-write",
        "-a",
        "on-failure",
        "exec",
        "do the work",
    ]


def test_sandbox_approval_do_not_affect_capability_probe() -> None:
    """Guardrail values live in fields, not command, so the --help probe ignores them."""
    codex = next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")
    # A help text mentioning only the static command flag (`exec`) — not the
    # `workspace-write`/`on-failure` values — must still confirm the runner.
    cap = runner.probe_capability(codex, run=lambda _binary: "usage: codex exec [prompt]")
    assert cap.flag_ok is True


# --- availability + selection ----------------------------------------------


def test_is_available_handoff_is_always_true() -> None:
    """The handoff runner is usable even when nothing is on PATH."""
    assert runner.is_available(RunnerSpec(MANUAL_RUNNER, HANDOFF), which=_which_none) is True


def test_is_available_headless_follows_path() -> None:
    """A headless runner is available only when its binary is on PATH."""
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER))
    assert runner.is_available(spec, which=_which_only("codex")) is True
    assert runner.is_available(spec, which=_which_none) is False


def test_select_explicit_name_wins() -> None:
    """An explicit name is honored even when that binary is not on PATH."""
    spec = runner.select_runner(BUILTIN_RUNNERS, "codex", which=_which_none)
    assert spec.name == "codex"


def test_select_explicit_unknown_raises() -> None:
    """An explicit but unknown runner name is an error, not a silent fallback."""
    with pytest.raises(ValueError, match="unknown runner"):
        runner.select_runner(BUILTIN_RUNNERS, "nope", which=_which_none)


def test_auto_prefers_first_available_in_order() -> None:
    """Auto walks claude -> codex -> copilot; codex present but not claude picks codex."""
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", which=_which_only("codex", "copilot"))
    assert spec.name == "codex"


def test_auto_falls_back_to_manual_handoff_when_none_present() -> None:
    """No big-3 CLI on PATH: never guess — fall back to the manual handoff runner."""
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", which=_which_none)
    assert spec.name == MANUAL_RUNNER
    assert spec.kind == HANDOFF


def test_none_choice_behaves_like_auto() -> None:
    """No explicit choice detects like auto (claude present is selected)."""
    spec = runner.select_runner(BUILTIN_RUNNERS, None, which=_which_only("claude"))
    assert spec.name == "claude"


# --- capability probe (basicly-bveo) ----------------------------------------


def test_headless_flags_excludes_placeholders() -> None:
    """The probed flag tokens are the static args, not the prompt/model placeholders."""
    spec = RunnerSpec(
        "acme", HEADLESS, ("acme", "run", runner.MODEL_PLACEHOLDER, PROMPT_PLACEHOLDER)
    )
    assert runner._headless_flags(spec) == ["run"]


def test_probe_capability_confirms_a_present_flag() -> None:
    """The flag appearing in --help output confirms capability."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    cap = runner.probe_capability(spec, run=lambda _b: "usage: claude [-p, --print] ...")
    assert cap.reachable and cap.flag_ok


def test_probe_capability_flags_a_dropped_flag() -> None:
    """A binary that ran but no longer mentions the flag is not capable."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    cap = runner.probe_capability(spec, run=lambda _b: "usage: claude [--chat] (no print flag)")
    assert cap.reachable and not cap.flag_ok
    assert "-p" in cap.detail


def test_probe_capability_assumes_capable_when_unprobeable() -> None:
    """A probe that could not run never false-skips a working agent."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    cap = runner.probe_capability(spec, run=lambda _b: None)
    assert cap.reachable is False and cap.flag_ok is True


def test_probe_capability_handoff_is_trivially_capable() -> None:
    """A handoff runner has no binary to probe and is always capable."""
    assert runner.probe_capability(RunnerSpec(MANUAL_RUNNER, HANDOFF)).flag_ok is True


def test_is_capable_requires_both_path_and_flag() -> None:
    """is_capable is on-PATH AND flag-confirmed."""
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER))
    assert runner.is_capable(spec, which=_which_only("codex"), run=lambda _b: "codex exec ...")
    assert not runner.is_capable(spec, which=_which_only("codex"), run=lambda _b: "codex chat")
    assert not runner.is_capable(spec, which=_which_none, run=lambda _b: "codex exec")


def test_auto_skips_an_incapable_runner() -> None:
    """Auto skips a runner on PATH whose probe fails and takes the next capable one."""
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", capable=lambda s: s.name == "codex")
    assert spec.name == "codex"


def test_auto_falls_back_to_manual_when_none_capable() -> None:
    """No capable big-3 runner: fall back to the manual handoff, never guess."""
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto", capable=lambda _s: False)
    assert spec.name == MANUAL_RUNNER


def test_explicit_choice_is_not_probe_gated() -> None:
    """An explicit name is honored even when its capability probe would fail."""
    spec = runner.select_runner(BUILTIN_RUNNERS, "claude", capable=lambda _s: False)
    assert spec.name == "claude"


# --- run --------------------------------------------------------------------


def test_run_dry_run_returns_argv_without_executing(monkeypatch: pytest.MonkeyPatch) -> None:
    """A dry run returns the exact argv and never touches subprocess."""

    def boom(*_a, **_k):
        raise AssertionError("subprocess.run must not be called on a dry run")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "hello", Path("/tmp"), dry_run=True)
    assert result.executed is False
    assert result.command == ("claude", "-p", "hello")
    assert result.duration_s is None  # nothing ran, so no wall-clock


def test_run_handoff_never_executes(monkeypatch: pytest.MonkeyPatch) -> None:
    """A handoff run shells out to nothing and flags the handoff."""

    def boom(*_a, **_k):
        raise AssertionError("a handoff runner must not execute anything")

    monkeypatch.setattr(runner.subprocess, "run", boom)
    result = runner.run(RunnerSpec(MANUAL_RUNNER, HANDOFF), "hello", Path("/tmp"))
    assert result.handoff is True
    assert result.executed is False
    assert result.command == ()
    assert result.duration_s is None  # nothing ran, so no wall-clock


def _patch_popen(
    monkeypatch: pytest.MonkeyPatch, *, stdout: str = "", stderr: str = "", returncode: int = 0
) -> dict[str, object]:
    """Fake the dispatch subprocess, recording the argv, the Popen kwargs and the input.

    ``run`` drives ``Popen`` rather than ``subprocess.run`` so a timed-out
    dispatch can be killed as a whole process group (basicly-kjc5.15); the prompt
    therefore arrives through ``communicate(input=...)`` instead of a kwarg.
    """
    captured: dict[str, object] = {}

    class _Proc:
        pid = 1234

        def __init__(self) -> None:
            self.returncode = returncode

        def communicate(self, input=None, timeout=None):
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
    """A live run passes the argv/cwd to subprocess and captures the result."""
    captured = _patch_popen(monkeypatch, stdout="done")
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "build it", Path("/work"))

    assert captured["argv"] == ["claude", "-p", "build it"]
    assert captured["cwd"] == Path("/work")
    assert captured["input"] is None  # arg injection, not stdin
    assert result.executed is True
    assert result.returncode == 0
    assert result.stdout == "done"
    assert isinstance(result.duration_s, float) and result.duration_s >= 0


def test_run_redacts_secrets_from_captured_output(monkeypatch: pytest.MonkeyPatch) -> None:
    """A secret an agent echoes on stdout/stderr is redacted at the source (basicly-3p2i)."""
    token = "ghp_" + "a" * 30
    _patch_popen(monkeypatch, stdout=f"pushed with {token}", stderr=f"warning near {token}")
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "go", Path("/work"))

    assert token not in result.stdout and "<redacted:github-token>" in result.stdout
    assert token not in result.stderr and "<redacted:github-token>" in result.stderr


def test_run_stdin_injection_passes_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stdin runner sends the prompt via subprocess input, not argv."""
    captured = _patch_popen(monkeypatch)
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin")
    runner.run(spec, "prompt on stdin", Path("/work"))

    assert captured["argv"] == ["x", "--headless"]
    assert captured["input"] == "prompt on stdin"
    assert captured["stdin"] is subprocess.PIPE  # a prompt needs a writable pipe


def test_git_identity_env_none_without_identity() -> None:
    """No bot identity configured -> no env overrides (basicly-smzg)."""
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    assert runner.git_identity_env(spec) is None


def test_git_identity_env_pins_all_four_vars() -> None:
    """A configured bot identity pins both author and committer name/email."""
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
    """run() overlays the bot identity on the inherited env, not replacing it (basicly-smzg)."""
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
    assert env["EXISTING_VAR"] == "kept"  # overlay, not replacement


def test_run_without_identity_adds_only_attribution(monkeypatch: pytest.MonkeyPatch) -> None:
    """Without a bot identity only the br attribution overlay is added.

    The basicly-smzg inherit-unchanged contract, extended by basicly-kjc5.3.
    """
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

    # Everything but the attribution overlay is the inherited environment,
    # including the absence of any GIT_AUTHOR/COMMITTER identity override.
    assert strip(env) == strip(dict(os.environ))


# --- usage capture + extraction (basicly-kjc5.1) -----------------------------


def _claude_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "claude")


def _claude_json_spec() -> RunnerSpec:
    """A consumer pinning the older single-object envelope (still supported)."""
    return _claude_spec().__class__(
        "claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER), usage_format=CLAUDE_JSON
    )


def _codex_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "codex")


def _executed(spec: RunnerSpec, stdout: str, stderr: str = "") -> RunResult:
    return RunResult(
        spec.name, (spec.name,), executed=True, returncode=0, stdout=stdout, stderr=stderr
    )


# Captured from a live `claude -p ... --output-format json` probe (2026-07-22),
# trimmed to the fields extraction reads plus representative noise.
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

# The documented `codex exec --json` JSONL event stream: usage rides on
# turn.completed events; cached_input_tokens is a subset of input_tokens.
# Shape of `claude -p ... --output-format stream-json --verbose`, pinned against a
# live probe (2026-07-25): a plain-text warning line, then one event per turn
# carrying that turn's usage, then the same result object the non-streaming
# envelope emits. Event kinds beyond assistant/result appear (system,
# rate_limit_event) and a non-JSON line can precede the stream, so the reader must
# skip what it does not recognise. The second assistant turn is the occupancy
# view; the result event's cache_read re-count is the cumulative cost view
# (basicly-kjc5.14).
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
        "total_cost_usd": 0.136147,
        "usage": {
            "input_tokens": 6,
            "cache_creation_input_tokens": 6000,
            "cache_read_input_tokens": 15496,
            "output_tokens": 108,
        },
    }),
])

_CODEX_EVENTS = "\n".join([
    '{"type":"thread.started","thread_id":"t1"}',
    '{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}',
    '{"type":"turn.completed","usage":'
    '{"input_tokens":24763,"cached_input_tokens":24448,"output_tokens":122}}',
    '{"type":"turn.completed","usage":'
    '{"input_tokens":100,"cached_input_tokens":50,"output_tokens":7}}',
])


def test_format_command_default_omits_usage_flags() -> None:
    """Plain-text consumers (rubric judging, review) get the unflagged argv."""
    assert runner.format_command(_claude_spec(), "go") == ["claude", "-p", "go"]


def test_format_command_capture_usage_appends_claude_flags() -> None:
    """A usage-capturing claude dispatch asks for the per-turn stream.

    stream-json is refused under -p without --verbose, so the flag is part of
    the contract, not decoration (basicly-kjc5.14).
    """
    argv = runner.format_command(_claude_spec(), "go", capture_usage=True)
    assert argv == ["claude", "-p", "go", "--output-format", "stream-json", "--verbose"]


def test_format_command_capture_usage_keeps_the_pinned_json_envelope() -> None:
    """A consumer pinned to claude-json still gets the single-object flags."""
    argv = runner.format_command(_claude_json_spec(), "go", capture_usage=True)
    assert argv == ["claude", "-p", "go", "--output-format", "json"]


def test_format_command_capture_usage_appends_codex_json_trailing() -> None:
    """Codex gets `--json` trailing, so the flag stays inside the exec subcommand."""
    argv = runner.format_command(_codex_spec(), "go", capture_usage=True)
    assert argv[-1] == "--json"
    assert argv.index("exec") < argv.index("--json")


def test_format_command_capture_usage_without_format_leaves_argv_unchanged() -> None:
    """Copilot reports no token usage (probed 2026-07-22): no flags to append."""
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    assert copilot.usage_format is None
    argv = runner.format_command(copilot, "go", capture_usage=True)
    assert argv == runner.format_command(copilot, "go")


def test_format_command_unknown_usage_format_raises() -> None:
    """A hand-built spec with a bogus format fails loudly, not silently unmetered."""
    spec = RunnerSpec("x", HEADLESS, ("x", PROMPT_PLACEHOLDER), usage_format="bogus")
    with pytest.raises(ValueError, match="usage_format"):
        runner.format_command(spec, "go", capture_usage=True)


def test_usage_format_does_not_affect_capability_probe() -> None:
    """Usage flags live outside spec.command, so the --help probe ignores them."""
    cap = runner.probe_capability(_claude_spec(), run=lambda _binary: "usage: claude -p [prompt]")
    assert cap.flag_ok is True


def test_run_capture_usage_executes_with_usage_flags(monkeypatch: pytest.MonkeyPatch) -> None:
    """run(capture_usage=True) invokes the argv with the usage-report flags."""
    captured = _patch_popen(monkeypatch)
    runner.run(_claude_spec(), "go", Path("/work"), capture_usage=True)
    assert captured["argv"] == ["claude", "-p", "go", "--output-format", "stream-json", "--verbose"]


def test_extract_usage_claude_reads_tokens_and_cost() -> None:
    """The claude result object yields summed usage tokens plus total_cost_usd."""
    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_RESULT))
    assert usage is not None
    assert usage.tokens == 2 + 5960 + 15496 + 17
    assert usage.cost == pytest.approx(0.136147)
    assert usage.estimated is False


def test_extract_usage_claude_without_cost_field() -> None:
    """A usage block without total_cost_usd still reports tokens, cost null."""
    stdout = json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5}})
    spec = _claude_json_spec()
    usage = runner.extract_usage(spec, _executed(spec, stdout))
    assert usage == runner.Usage(tokens=15, cost=None, estimated=False)


def test_extract_usage_claude_unparseable_falls_back_to_estimate() -> None:
    """Non-JSON output (e.g. an overridden command) degrades to the chars/4 estimate."""
    result = _executed(_claude_spec(), "plain text answer", stderr="warn")
    usage = runner.extract_usage(_claude_spec(), result)
    assert usage == runner.Usage(
        tokens=(len("plain text answer") + len("warn")) // 4, cost=None, estimated=True
    )


def test_extract_usage_claude_json_without_usage_block_estimates() -> None:
    """A parseable object missing the usage block still degrades to the estimate."""
    stdout = json.dumps({"type": "result", "result": "ok"})
    usage = runner.extract_usage(_claude_spec(), _executed(_claude_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_codex_sums_turns_excluding_cached() -> None:
    """Codex turn.completed events sum input+output; cached is a subset, not added."""
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert usage == runner.Usage(tokens=24763 + 122 + 100 + 7, cost=None, estimated=False)


def test_extract_usage_codex_without_usage_events_estimates() -> None:
    """An event stream with no turn.completed usage degrades to the estimate."""
    stdout = '{"type":"thread.started","thread_id":"t1"}\nnot json\n'
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_no_format_estimates_over_transcript() -> None:
    """A spec with no usage format (copilot) meters the transcript at chars/4."""
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    result = _executed(copilot, "x" * 100, stderr="y" * 20)
    assert runner.extract_usage(copilot, result) == runner.Usage(
        tokens=30, cost=None, estimated=True
    )


def test_extract_usage_none_when_nothing_executed() -> None:
    """A handoff or dry run has no transcript to meter: no usage, not a zero estimate."""
    handoff = RunResult(MANUAL_RUNNER, (), executed=False, handoff=True)
    assert runner.extract_usage(RunnerSpec(MANUAL_RUNNER, HANDOFF), handoff) is None
    dry = RunResult("claude", ("claude",), executed=False)
    assert runner.extract_usage(_claude_spec(), dry) is None


def test_builtin_usage_formats_pin_the_probed_capabilities() -> None:
    """The claude and codex builtins report usage; copilot does not (probed 2026-07-22)."""
    by_name = {s.name: s.usage_format for s in BUILTIN_RUNNERS}
    assert by_name["claude"] == CLAUDE_STREAM_JSON
    assert by_name["codex"] == CODEX_JSONL
    assert by_name["copilot"] is None
    assert by_name[MANUAL_RUNNER] is None


# --- Context windows and occupancy (basicly-kjc5.6, factory design D8) -------


def test_builtin_context_windows_follow_the_design_defaults() -> None:
    """Per-adapter windows from design §6; unknown agents get the smallest big-3."""
    by_name = {s.name: s.context_window for s in BUILTIN_RUNNERS}
    assert by_name["claude"] == 200_000
    assert by_name["codex"] == 400_000
    assert by_name["copilot"] == 128_000
    assert runner.DEFAULT_CONTEXT_WINDOW == 128_000


def test_context_occupancy_claude_json_is_unknowable() -> None:
    """The claude result usage block is session-cumulative (probed 2026-07-23).

    Treating it as occupancy would cross any ceiling on every healthy
    multi-turn run, so the meter must report unknowable, never that sum.
    """
    spec = _claude_json_spec()
    occupancy = runner.context_occupancy(spec, _executed(spec, _CLAUDE_RESULT))
    assert occupancy is None


def test_extract_usage_claude_stream_reads_the_result_event() -> None:
    """The cost view stays cumulative: it comes from the stream's result event."""
    spec = _claude_spec()
    usage = runner.extract_usage(spec, _executed(spec, _CLAUDE_STREAM))

    assert usage is not None
    assert usage.tokens == 6 + 6000 + 15496 + 108
    assert usage.cost == pytest.approx(0.136147)
    assert usage.estimated is False


def test_context_occupancy_claude_stream_reads_the_last_assistant_turn() -> None:
    """Occupancy is the final turn's window, not the cumulative cache re-count.

    The result event totals 21610 tokens against a ~15.5K final context; metering
    that sum would trip any ceiling on a healthy multi-turn run (basicly-kjc5.14).
    """
    spec = _claude_spec()
    occupancy = runner.context_occupancy(spec, _executed(spec, _CLAUDE_STREAM))

    assert occupancy == 2 + 40 + 15496 + 17
    cumulative = runner.extract_usage(spec, _executed(spec, _CLAUDE_STREAM))
    assert cumulative is not None and occupancy < cumulative.tokens


def test_context_occupancy_claude_stream_ignores_noise_and_partial_lines() -> None:
    """A killed dispatch leaves a truncated tail; the last whole turn still reads."""
    stdout = (
        "Reading prompt from stdin\n"
        '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":5}}}\n'
        '{"type":"assistant","message":{"usage":{"input_tok'
    )
    spec = _claude_spec()

    assert runner.context_occupancy(spec, _executed(spec, stdout)) == 15


def test_context_occupancy_claude_stream_is_none_without_a_turn() -> None:
    """No assistant turn parsed means unknowable — never a guess from stdout length."""
    spec = _claude_spec()
    stdout = '{"type":"system","subtype":"init"}'

    assert runner.context_occupancy(spec, _executed(spec, stdout)) is None


def test_extract_usage_claude_stream_without_a_result_event_estimates() -> None:
    """A stream cut off before its result event has no reported total to trust."""
    spec = _claude_spec()
    stdout = '{"type":"assistant","message":{"usage":{"input_tokens":10,"output_tokens":5}}}'
    usage = runner.extract_usage(spec, _executed(spec, stdout))

    assert usage is not None and usage.estimated is True


def test_context_occupancy_codex_reads_last_turn_only() -> None:
    """Codex occupancy is the last turn's tokens; summing turns is the cost view."""
    occupancy = runner.context_occupancy(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert occupancy == 100 + 7


def test_context_occupancy_never_falls_back_to_the_transcript_estimate() -> None:
    """No usage format or a parse miss yields None, never an estimate.

    Stdout length says nothing about window occupancy, and a false trigger
    would spin a phantom follow-up bead.
    """
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    assert runner.context_occupancy(copilot, _executed(copilot, "x" * 4000)) is None
    stdout = '{"type":"thread.started","thread_id":"t1"}\n'
    assert runner.context_occupancy(_codex_spec(), _executed(_codex_spec(), stdout)) is None


def test_context_occupancy_none_when_nothing_executed() -> None:
    """A handoff or dry run occupies no window."""
    handoff = RunResult(MANUAL_RUNNER, (), executed=False, handoff=True)
    assert runner.context_occupancy(RunnerSpec(MANUAL_RUNNER, HANDOFF), handoff) is None


# --- br attribution env (basicly-kjc5.3, D3) ----------------------------------


def test_br_attribution_env_names_agent_harness_and_model() -> None:
    """Dispatched agents carry br tier-1 attribution; model only when pinned."""
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
    """The dispatched subprocess sees the attribution overlay on the inherited env."""
    captured = _patch_popen(monkeypatch)
    runner.run(_claude_spec(), "go", Path("/work"))
    env = captured["env"]
    assert isinstance(env, dict)
    assert env["BR_AGENT_NAME"] == "claude"
    assert env["BR_HARNESS"] == "basicly-loop"


# --- runner_timeout hard kill (basicly-kjc5.7, design section 6) ----------------


class _HungProc:
    """A dispatch that blows the timeout, then yields its buffered output once killed."""

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
    """A hung dispatch is hard-killed and reported, never waited on forever."""
    hung = _HungProc()
    monkeypatch.setattr(runner.subprocess, "Popen", lambda *_a, **_k: hung)
    killed: list[int] = []
    monkeypatch.setattr(runner, "_kill_tree", lambda proc: killed.append(proc.pid))

    result = runner.run(_claude_spec(), "go", Path("/work"), timeout=1.0)

    assert result.timed_out is True
    assert result.executed is True
    assert result.returncode is None
    assert "partial" in result.stdout  # drained after the kill, not before it
    assert killed == [hung.pid]


# --- Portable process-tree kill on timeout (basicly-kjc5.15) -------------------


def test_run_starts_the_dispatch_in_its_own_session_on_posix(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Without its own session there is no group to kill: the flag is not optional."""
    monkeypatch.setattr(runner.os, "name", "posix")
    captured = _patch_popen(monkeypatch)
    runner.run(_claude_spec(), "go", Path("/work"))

    assert captured["start_new_session"] is True
    assert captured["creationflags"] == 0  # inert off Windows


def test_run_starts_a_windows_dispatch_in_its_own_process_group(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The Windows branch is unreachable on POSIX CI, so pin it directly."""
    monkeypatch.setattr(runner.os, "name", "nt")
    captured = _patch_popen(monkeypatch)
    runner.run(_claude_spec(), "go", Path("/work"))

    assert captured["creationflags"] == runner.CREATE_NEW_PROCESS_GROUP
    assert captured["start_new_session"] is False


class _Stubborn:
    """A tree that ignores the polite signal."""

    pid = 99

    def wait(self, timeout=None):
        raise subprocess.TimeoutExpired(("agent",), timeout or 0)


# POSIX-only signal number, referenced by the tests that *simulate* the POSIX
# branch. Windows' signal module has no SIGKILL and pyright resolves attributes
# per platform, so a direct reference is an error there even inside a test that
# skipif already excludes — pyright is static and does not read the marker. The
# fallback mirrors runner.CREATE_NEW_PROCESS_GROUP. runner.py itself needs no such
# guard: its POSIX branch sits after an `os.name == "nt"` early return, which
# pyright narrows (basicly-kjc5.54).
SIGKILL = getattr(signal, "SIGKILL", 9)


class _Polite:
    """A tree that exits on the polite signal."""

    pid = 99

    def wait(self, **_kwargs):
        return -15


def _record_signals(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    """Fake the POSIX process-group API so the branch is testable on any platform.

    The test already fakes ``os.name``, so it is a simulation rather than a real
    platform check — but ``os.getpgid``/``os.killpg`` do not exist on Windows and
    ``monkeypatch.setattr`` refuses to create an absent attribute, so the
    simulation needs ``raising=False`` to be installable there (basicly-kjc5.54).
    Keeping the test running on Windows is deliberate: it covers the branch's
    logic, which is worth checking everywhere even though it only executes on
    POSIX.
    """
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
    """A tree that ignores the polite signal is killed outright after the grace."""
    monkeypatch.setattr(runner, "KILL_GRACE_S", 0.01)
    signalled = _record_signals(monkeypatch)

    runner._kill_tree(cast("subprocess.Popen[str]", _Stubborn()))

    assert signalled == [signal.SIGTERM, SIGKILL]


def test_kill_tree_stops_at_the_polite_signal_when_the_group_goes_down(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A tree that exits on SIGTERM is never SIGKILLed — children get to clean up."""
    signalled = _record_signals(monkeypatch)

    runner._kill_tree(cast("subprocess.Popen[str]", _Polite()))

    assert signalled == [signal.SIGTERM]


def test_kill_tree_tolerates_a_dispatch_that_already_exited(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Racing the process's own exit is not an error worth propagating."""
    monkeypatch.setattr(runner.os, "name", "posix")
    # raising=False for the same reason as _record_signals: absent on Windows.
    monkeypatch.setattr(runner.os, "getpgid", lambda pid: pid, raising=False)

    def gone(_pgid, _signum):
        raise ProcessLookupError("no such process")

    monkeypatch.setattr(runner.os, "killpg", gone, raising=False)

    class _Gone:
        pid = 99

        def wait(self, **_kwargs):
            raise AssertionError("must not wait on a process already gone")

    runner._kill_tree(cast("subprocess.Popen[str]", _Gone()))  # no raise


def test_kill_tree_on_windows_walks_the_child_chain_with_taskkill(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Windows has no killpg: the tree comes down via taskkill /T."""
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
    """Its own session means Ctrl-C no longer reaches the agent: kill it explicitly."""

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
    """The stall is already routed: a held pipe must not hang the supervisor pass."""
    monkeypatch.setattr(runner, "KILL_GRACE_S", 0.01)

    class _Holder:
        def communicate(self, timeout=None):
            raise subprocess.TimeoutExpired(("agent",), timeout or 0)

    assert runner._drain(cast("subprocess.Popen[str]", _Holder())) == ("", "")


@pytest.mark.skipif(os.name == "nt", reason="POSIX process-group semantics")
def test_timeout_kills_a_grandchild_the_dispatch_spawned(tmp_path: Path) -> None:
    """The real thing: an agent's own child must not outlive the killed dispatch.

    The dispatch prints the pid of a process it spawned and then hangs. After the
    timeout that pid must be gone — before the group kill it survived, kept
    changing the lane's worktree, and was invisible to the queued stall.
    """
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
            break  # reaped: the group kill reached it
        time.sleep(0.05)
    else:  # pragma: no cover - only reached on a regression
        os.kill(grandchild, SIGKILL)
        pytest.fail(f"grandchild {grandchild} survived the dispatch timeout")


def test_record_dispatch_never_raises_on_a_spec_result_mismatch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A handoff spec with an executed result must record, not crash (basicly-kjc5.53).

    Telemetry sits on the critical path of every dispatch, so a defect in
    recording must never fail a landing. This mismatch is not hypothetical: on a
    machine with no agent CLI, ``select_runner`` resolves the handoff ``manual``
    runner while a caller's result still reports execution — which is exactly how
    CI reproduced it where a developer machine could not.
    """
    spec = runner.select_runner(runner.BUILTIN_RUNNERS, "manual")
    result = runner.RunResult("manual", (), executed=True, returncode=0, stdout="ok")
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-x", spec, result, prompt="p", phase="validate")

    history = runner.run_record.load_run_records(tmp_path) or {}
    (entry,) = history["basicly-x"]
    assert entry["command"] == []  # degraded, not fatal
    assert entry["agent"] == "manual"

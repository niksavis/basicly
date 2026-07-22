"""Tests for the agent-agnostic runner adapters (onb.7).

A runner only invokes an agent headless: it formats an exact argv (or hands off),
detects which agent to use, and captures output. These tests pin that behavior
and — crucially — that an unknown agent's command line is never guessed: `auto`
falls back to the manual handoff runner, which never shells out.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import runner
from basicly.runner import (
    BUILTIN_RUNNERS,
    CLAUDE_JSON,
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


def test_run_executes_and_captures(monkeypatch: pytest.MonkeyPatch) -> None:
    """A live run passes the argv/cwd to subprocess and captures the result."""
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = "done"
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["cwd"] = kwargs.get("cwd")
        captured["input"] = kwargs.get("input")
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
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

    class _Proc:
        returncode = 0
        stdout = f"pushed with {token}"
        stderr = f"warning near {token}"

    monkeypatch.setattr(runner.subprocess, "run", lambda _argv, **_kw: _Proc())
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    result = runner.run(spec, "go", Path("/work"))

    assert token not in result.stdout and "<redacted:github-token>" in result.stdout
    assert token not in result.stderr and "<redacted:github-token>" in result.stderr


def test_run_stdin_injection_passes_prompt_on_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """A stdin runner sends the prompt via subprocess input, not argv."""
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **kwargs):
        captured["argv"] = argv
        captured["input"] = kwargs.get("input")
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    spec = RunnerSpec("x", HEADLESS, ("x", "--headless"), prompt_via="stdin")
    runner.run(spec, "prompt on stdin", Path("/work"))

    assert captured["argv"] == ["x", "--headless"]
    assert captured["input"] == "prompt on stdin"


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
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(_argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
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


def test_run_leaves_env_untouched_without_identity(monkeypatch: pytest.MonkeyPatch) -> None:
    """No bot identity -> env stays None so the child inherits unchanged (basicly-smzg)."""
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(_argv, **kwargs):
        captured["env"] = kwargs.get("env")
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    spec = RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER))
    runner.run(spec, "go", Path("/work"))

    assert captured["env"] is None


# --- usage capture + extraction (basicly-kjc5.1) -----------------------------


def _claude_spec() -> RunnerSpec:
    return next(s for s in BUILTIN_RUNNERS if s.name == "claude")


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
    """A usage-capturing claude dispatch asks for the JSON result object."""
    argv = runner.format_command(_claude_spec(), "go", capture_usage=True)
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
    captured: dict[str, object] = {}

    class _Proc:
        returncode = 0
        stdout = ""
        stderr = ""

    def fake_run(argv, **_k):
        captured["argv"] = argv
        return _Proc()

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    runner.run(_claude_spec(), "go", Path("/work"), capture_usage=True)
    assert captured["argv"] == ["claude", "-p", "go", "--output-format", "json"]


def test_extract_usage_claude_reads_tokens_and_cost() -> None:
    """The claude result object yields summed usage tokens plus total_cost_usd."""
    usage = runner.extract_usage(_claude_spec(), _executed(_claude_spec(), _CLAUDE_RESULT))
    assert usage is not None
    assert usage.tokens == 2 + 5960 + 15496 + 17
    assert usage.cost == pytest.approx(0.136147)
    assert usage.estimated is False


def test_extract_usage_claude_without_cost_field() -> None:
    """A usage block without total_cost_usd still reports tokens, cost null."""
    stdout = json.dumps({"usage": {"input_tokens": 10, "output_tokens": 5}})
    usage = runner.extract_usage(_claude_spec(), _executed(_claude_spec(), stdout))
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
    assert by_name["claude"] == CLAUDE_JSON
    assert by_name["codex"] == CODEX_JSONL
    assert by_name["copilot"] is None
    assert by_name[MANUAL_RUNNER] is None

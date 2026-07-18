"""Tests for the agent-agnostic runner adapters (onb.7).

A runner only invokes an agent headless: it formats an exact argv (or hands off),
detects which agent to use, and captures output. These tests pin that behavior
and — crucially — that an unknown agent's command line is never guessed: `auto`
falls back to the manual handoff runner, which never shells out.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import runner
from basicly.runner import (
    BUILTIN_RUNNERS,
    HANDOFF,
    HEADLESS,
    MANUAL_RUNNER,
    PROMPT_PLACEHOLDER,
    RunnerSpec,
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

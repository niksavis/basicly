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

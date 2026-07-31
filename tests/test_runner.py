"""Tests for the agent-agnostic runner adapters (onb.7).

A runner only invokes an agent headless: it formats an exact argv (or hands off),
detects which agent to use, and captures output. These tests pin that behavior
and — crucially — that an unknown agent's command line is never guessed: `auto`
falls back to the manual handoff runner, which never shells out.
"""

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

from basicly import runner
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
    """Model injection then deny-tool injection both land after the binary, model first."""
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
    """Approval unset injects only the sandbox flag."""
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER), sandbox="read-only")
    assert runner.format_command(spec, "go") == ["codex", "--sandbox", "read-only", "exec", "go"]


def test_codex_builtin_defaults_render_workspace_write_never() -> None:
    """The shipped codex adapter carries the guardrail defaults into its rendered argv.

    ``never`` is pinned, not ``on-failure``: the latter is absent from the CLI's
    approval enum, so it made every codex dispatch exit 2 at argument parsing
    (basicly-jr0l.38).
    """
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
    """Guardrail values live in fields, not command, so the --help flag probe ignores them.

    They are checked separately, and only against a help text that actually
    enumerates the flag — see the guardrail tests below.
    """
    codex = next(s for s in runner.BUILTIN_RUNNERS if s.name == "codex")
    # A help text mentioning only the static command flag (`exec`) — not the
    # `--sandbox`/`--ask-for-approval` enums — must still confirm the runner.
    cap = runner.probe_capability(codex, run=lambda _binary: "usage: codex exec [prompt]")
    assert cap.flag_ok is True


def _builtin(name: str) -> RunnerSpec:
    return next(s for s in runner.BUILTIN_RUNNERS if s.name == name)


# --- guardrail enum validation (basicly-jr0l.38) -----------------------------
#
# Verbatim `codex --help` extracts, at codex-cli 0.146.0. clap renders an enum
# two ways and codex uses both, so both are fixtures: `--sandbox` inline, and
# `--ask-for-approval` as an indented bullet list whose entries wrap.

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
    """`--sandbox` documents its enum inline, in brackets on its own line."""
    assert runner.possible_values(CODEX_HELP, "--sandbox") == (
        "read-only",
        "workspace-write",
        "danger-full-access",
    )


def test_possible_values_reads_the_bulleted_enum_rendering() -> None:
    """`--ask-for-approval` documents its enum as bullets whose descriptions wrap.

    The wrapped continuation lines ("approval. Will escalate...") must not be
    read as values, and the list must not bleed into the next option entry.
    """
    assert runner.possible_values(CODEX_HELP, "--ask-for-approval") == (
        "untrusted",
        "on-request",
        "never",
    )


def test_possible_values_is_none_when_the_flag_enumerates_nothing() -> None:
    """An absent flag and a flag with no enum are both "cannot tell", not "nothing accepted"."""
    assert runner.possible_values(CODEX_HELP, "--no-such-flag") is None
    assert runner.possible_values(CODEX_HELP, "--add-dir") is None


def test_check_guardrails_names_the_rejected_approval_and_the_accepted_set() -> None:
    """The regression: `on-failure` is not in the enum, so the check reports it by name.

    This is the exact spec that shipped, and it exited 2 with no output on every
    dispatch. The message must name the offending value so the fix is obvious
    without re-running the CLI.
    """
    spec = replace(_builtin("codex"), approval="on-failure")
    (problem,) = runner.check_guardrails(spec, help_text=CODEX_HELP)
    assert "on-failure" in problem
    assert "--ask-for-approval" in problem
    assert "untrusted, on-request, never" in problem


def test_check_guardrails_names_a_rejected_sandbox() -> None:
    """The inline-rendered flag is validated the same way."""
    spec = replace(_builtin("codex"), sandbox="wide-open")
    (problem,) = runner.check_guardrails(spec, help_text=CODEX_HELP)
    assert "wide-open" in problem
    assert "read-only, workspace-write, danger-full-access" in problem


def test_check_guardrails_passes_the_shipped_codex_spec() -> None:
    """The adapter as shipped must be accepted by the CLI it targets."""
    assert runner.check_guardrails(_builtin("codex"), help_text=CODEX_HELP) == ()


def test_check_guardrails_stays_silent_without_positive_evidence() -> None:
    """An unreadable probe or a help text without the enums never disproves a spec.

    Same rule as the flag probe: guessing would false-skip a working agent.
    """
    codex = _builtin("codex")
    assert runner.check_guardrails(codex, help_text=None) == ()
    assert runner.check_guardrails(codex, help_text="usage: codex exec [prompt]") == ()
    assert runner.probe_guardrails(codex, run=lambda _binary: None) == ()


def test_probe_capability_fails_a_spec_the_cli_would_reject() -> None:
    """A rejected guardrail makes the runner not capable, so `auto` skips it.

    Selection falls through to the next candidate rather than picking an adapter
    whose every dispatch dies at argument parsing.
    """
    spec = replace(_builtin("codex"), approval="on-failure")
    cap = runner.probe_capability(spec, run=lambda _binary: CODEX_HELP)
    assert cap.reachable is True
    assert cap.flag_ok is False
    assert "on-failure" in cap.detail
    capable = runner.is_capable(spec, which=lambda _b: "/usr/bin/codex", run=lambda _b: CODEX_HELP)
    assert capable is False


# --- decider confinement (basicly-kjc5.16) ----------------------------------
#
# One case per supported agent family, asserted on the rendered argv rather than
# on the field: what confines the decider is the flag the CLI actually receives.


def test_confine_for_decider_denies_claudes_whole_tool_surface() -> None:
    """Claude gets one --disallowedTools naming every read, write, exec and network tool."""
    confined = runner.confine_for_decider(_builtin("claude"))
    assert confined is not None
    argv = runner.format_command(confined, "judge")
    assert argv[0] == "claude"
    assert argv[1] == "--disallowedTools"
    denied = set(argv[2 : argv.index("-p")])
    # The three that matter: no shell (so it cannot run br), no write, no read
    # beyond the corpus already in its prompt.
    assert {"Bash", "Write", "Read"} <= denied
    assert "judge" in argv


def test_confine_for_decider_denies_copilot_shell_write_and_read() -> None:
    """Copilot gets one --deny-tool= per class, in its own vocabulary.

    ``read`` is the one that bounds the corpus: with only shell and write denied,
    a probe showed copilot falling back to its native read tool and answering
    from a file outside the prompt (basicly-jr0l.27).
    """
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
    """Codex has no tool-deny flag, so it is confined by sandbox instead of blocklist.

    The sandbox drops to ``read-only``; approval stays ``never``, as the builtin
    already pins — headless exec has no approver, so an escalation must fail
    closed instead of waiting for one.
    """
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
    """Confinement only ever subtracts capability, so a baseline deny survives it.

    Copilot's builtin is loaded with the permissions.yaml deny-list, which can name
    a class this overlay does not — replacing it would hand the decider *more* than
    a normal lane gets.
    """
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
    """A headless agent with neither a deny style nor a sandbox cannot be bounded.

    None is the signal decisions.invoke_decider turns into an abstention — better
    a human answers than an unconfined agent does.
    """
    unknown = RunnerSpec("mystery", HEADLESS, ("mystery", PROMPT_PLACEHOLDER))
    assert runner.confine_for_decider(unknown) is None


def test_confine_for_decider_leaves_a_handoff_unchanged() -> None:
    """A handoff has no argv to carry flags and executes nothing, so there is nothing to confine."""
    handoff = RunnerSpec(MANUAL_RUNNER, HANDOFF)
    assert runner.confine_for_decider(handoff) is handoff


def test_deny_tools_without_a_style_raises_rather_than_emitting_a_flag() -> None:
    """Denials the binary cannot read must not be silently dropped onto its argv."""
    spec = RunnerSpec("mystery", HEADLESS, ("mystery", PROMPT_PLACEHOLDER), deny_tools=("write",))
    with pytest.raises(ValueError, match="deny_style"):
        runner.format_command(spec, "go")


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


def test_auto_resolves_ambiently_to_the_handoff_on_every_machine() -> None:
    """`auto` resolves the same here as on CI — through the real PATH, not a stub.

    This is the check behind the conftest fixture (basicly-kjc5.55), and the only
    one in the suite that would answer differently on a developer box: with
    ``claude`` on PATH and the fixture removed, ``auto`` picks the claude adapter
    and this fails. Its neighbours above pin the same logic through an injected
    ``which``, which by construction cannot notice the machine at all — that is
    exactly how the local/CI split stayed invisible long enough to hide
    basicly-kjc5.53.
    """
    spec = runner.select_runner(BUILTIN_RUNNERS, "auto")
    assert spec.name == MANUAL_RUNNER
    assert spec.kind == HANDOFF
    # And the dispatch path taken from that resolution: a handoff runs nothing, so
    # anything downstream reading a run result must cope with executed=False.
    result = runner.run(spec, "do the work", Path())
    assert result.handoff is True
    assert result.executed is False
    assert result.returncode is None


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


def test_run_arg_prompt_closes_stdin(monkeypatch: pytest.MonkeyPatch) -> None:
    """An arg-prompt dispatch gets stdin closed, never inherited (basicly-jr0l.36).

    Popen's ``stdin=None`` inherits the parent's, so an agent CLI that reads stdin
    for extra context blocks on the supervisor's own stdin until the dispatch
    timeout — codex exec does this — and the stall is indistinguishable from a
    wedged lane. The prompt is already on the argv, so DEVNULL is the contract.
    """
    captured = _patch_popen(monkeypatch)
    spec = RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER))
    runner.run(spec, "do the thing", Path("/work"))

    assert captured["stdin"] is subprocess.DEVNULL
    assert captured["input"] is None  # nothing to write when the prompt is on argv


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

# Two `turn.completed` usage objects captured verbatim from live `codex exec
# --json` probes of codex-cli 0.146.0 (2026-07-31 and 2026-07-29,
# basicly-jr0l.37), each paired with the `total_tokens` codex's own session
# rollout recorded for that same turn. That pairing is the evidence for how the
# fields relate: the identity input_tokens + output_tokens == total_tokens holds
# on both, and on all four turns this machine has recorded. So
# `cached_input_tokens` is a subset of `input_tokens`, and
# `reasoning_output_tokens` a subset of `output_tokens` — 12764 + 155 == 12919
# even though 147 of those 155 output tokens were reasoning, and the visible
# answer really was 4 characters long.
#
# The first probe forced a **non-zero** reasoning count
# (`model_reasoning_effort=high` on multi-step arithmetic). Every earlier sample
# on this machine reported 0, which is why the subset question could not be
# settled from existing data — and why the fixture this replaced, composed from
# the documented shape and never probed, carried no `cache_write_input_tokens`
# and no `reasoning_output_tokens` at all, which is how the dropped-split defect
# survived. Nothing from the prompt or the answer is copied here; the usage
# objects are pure counts.
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
    """A codex `--json` stream carrying *usages*, wrapped in the real event kinds.

    The non-usage events are what a live run interleaves, so the reader has to
    skip them rather than assume a stream of nothing but `turn.completed`.
    """
    lines = ['{"type":"thread.started","thread_id":"t1"}']
    for usage in usages:
        lines.append('{"type":"item.completed","item":{"type":"agent_message","text":"ok"}}')
        lines.append(json.dumps({"type": "turn.completed", "usage": usage}))
    return "\n".join(lines)


_CODEX_EVENTS = _codex_stream(*(usage for usage, _total in _CODEX_TURNS))


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
    """A spec reporting no usage has no flags to append: the argv is untouched."""
    spec = RunnerSpec("acme", HEADLESS, ("acme", PROMPT_PLACEHOLDER))
    assert spec.usage_format is None
    argv = runner.format_command(spec, "go", capture_usage=True)
    assert argv == runner.format_command(spec, "go")


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


@pytest.mark.parametrize(("turn", "total_tokens"), _CODEX_TURNS)
def test_extract_usage_codex_total_matches_the_cli_own_total(turn: dict, total_tokens: int) -> None:
    """A single observed turn totals exactly what codex itself accounted for it.

    The identity that settles the summation semantics: codex's session rollout
    recorded `total_tokens` for this very turn, and input + output reproduces it
    to the token. Any addend beyond those two would overshoot it.
    """
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert usage.tokens == total_tokens
    assert usage.estimated is False


def test_extract_usage_codex_records_reasoning_without_adding_it() -> None:
    """`reasoning_output_tokens` lands on the split but never in the total.

    Measured, not assumed (basicly-jr0l.37): the probed turn spent 147 of its 155
    output tokens on reasoning, so summing the two would double-count 147 tokens
    and inflate a 12919-token turn to 13066.
    """
    turn, total_tokens = _CODEX_TURNS[0]
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert usage.reasoning_tokens == 147
    assert usage.tokens == total_tokens
    assert usage.tokens != total_tokens + 147
    # Subset of output, so the residue is the answer plus its framing.
    assert usage.output_tokens is not None and usage.reasoning_tokens <= usage.output_tokens


def test_extract_usage_codex_records_the_cache_split_without_adding_it() -> None:
    """Cached input is the portion *inside* `input_tokens`, not a separate addend.

    `input_tokens` is the superset (the same convention copilot's `inputTokens`
    follows), so the uncached remainder the pricing model needs is derivable as
    input minus cache-read rather than stored a fourth time — and adding
    cache-read back in would report 22903 tokens for a 12919-token turn.
    """
    turn, total_tokens = _CODEX_TURNS[0]
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _codex_stream(turn)))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12764, 155)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (9984, 0)
    assert usage.tokens == total_tokens
    assert usage.tokens != total_tokens + 9984
    # The uncached remainder is derivable, which is why it is not stored a fourth time.
    assert usage.input_tokens is not None and usage.cache_read_tokens is not None
    assert usage.input_tokens - usage.cache_read_tokens == 2780


def test_extract_usage_codex_keeps_cache_write_out_of_the_total() -> None:
    """Cache-write is recorded and, like cache-read, is not added to the total.

    Synthetic on purpose, and the one codex assertion here **not** backed by an
    observed number: every turn recorded on this machine reported
    `cache_write_input_tokens` 0, so the total_tokens identity cannot speak to it.
    The convention comes from the semantics the rest of the mapping follows —
    cache-written tokens are prompt tokens, so they sit inside `input_tokens`,
    which is verified outright on the copilot side (`inputTokens == input +
    cacheRead + cacheWrite` on 15 stores). Pinned so a build that disagrees
    reddens a test instead of silently under-counting a cache-warming turn.
    """
    stream = _codex_stream({
        "input_tokens": 1000,
        "cached_input_tokens": 600,
        "cache_write_input_tokens": 300,
        "output_tokens": 20,
        "reasoning_output_tokens": 8,
    })
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stream))
    assert usage is not None
    assert usage.cache_write_tokens == 300
    assert usage.tokens == 1000 + 20


def test_extract_usage_codex_sums_the_split_across_turns() -> None:
    """A multi-turn stream meters once, per kind, over every turn's usage."""
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (12764 + 16824, 155 + 5)
    assert (usage.cache_read_tokens, usage.cache_write_tokens) == (9984 + 10496, 0)
    assert usage.reasoning_tokens == 147 + 0
    assert usage.tokens == 12764 + 155 + 16824 + 5
    assert usage.cost is None and usage.credits is None


def test_extract_usage_codex_leaves_an_unreported_kind_null() -> None:
    """A build that emits no cache or reasoning counts records null, never zero.

    0.146.0 reports a real `reasoning_output_tokens` of 0 for a turn that did no
    reasoning, so a fabricated 0 would be indistinguishable from that measurement.
    """
    stream = _codex_stream({"input_tokens": 100, "output_tokens": 7})
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stream))
    assert usage is not None
    assert (usage.input_tokens, usage.output_tokens) == (100, 7)
    assert usage.cache_read_tokens is None
    assert usage.cache_write_tokens is None
    assert usage.reasoning_tokens is None
    assert usage.tokens == 107


def test_extract_usage_codex_without_usage_events_estimates() -> None:
    """An event stream with no turn.completed usage degrades to the estimate."""
    stdout = '{"type":"thread.started","thread_id":"t1"}\nnot json\n'
    usage = runner.extract_usage(_codex_spec(), _executed(_codex_spec(), stdout))
    assert usage is not None
    assert usage.estimated is True


def test_extract_usage_no_format_estimates_over_transcript() -> None:
    """A spec with no usage format meters the transcript at chars/4."""
    spec = RunnerSpec("acme", HEADLESS, ("acme", PROMPT_PLACEHOLDER))
    result = _executed(spec, "x" * 100, stderr="y" * 20)
    assert runner.extract_usage(spec, result) == runner.Usage(tokens=30, cost=None, estimated=True)


# --- copilot: usage measured from its own session store (basicly-2rn9) -------


def test_format_command_capture_usage_keys_the_copilot_session_store() -> None:
    """A metered copilot dispatch supplies the session UUID and keeps stdout plain.

    `--session-id` sets a *new* session's id, so the store path is known before
    the store exists — and no `--output-format json` is appended, which is what
    lets the rubric judge's plain-text parser survive a metered dispatch.
    """
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    argv = runner.format_command(copilot, "go", capture_usage=True, session_id="sid-1")
    assert argv[-2:] == ["--session-id", "sid-1"]
    assert "--output-format" not in argv
    assert argv[:-2] == runner.format_command(copilot, "go")


def test_format_command_copilot_without_a_session_id_omits_the_flag() -> None:
    """No session id means no store to read, so the flag is left off entirely.

    An empty `--session-id` would be a broken argv, and a fabricated one would
    name a store that never gets written. That dispatch meters by estimate.
    """
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    argv = runner.format_command(copilot, "go", capture_usage=True)
    assert argv == runner.format_command(copilot, "go")


def test_run_mints_a_session_id_for_a_metered_copilot_dispatch(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """run() supplies the store key itself and hands the same value back on the result."""
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    captured = _patch_popen(monkeypatch)
    result = runner.run(copilot, "go", Path("/work"), capture_usage=True)
    assert result.session_id is not None
    argv = cast(list[str], captured["argv"])
    assert argv[-2:] == ["--session-id", result.session_id]
    # A real UUID, not a placeholder: copilot rejects anything else, and two
    # dispatches must never share a store.
    assert uuid.UUID(result.session_id).version == 4


def test_run_without_capture_usage_keys_no_copilot_store(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An unmetered dispatch keys no store, so it never joins another run's usage."""
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    captured = _patch_popen(monkeypatch)
    result = runner.run(copilot, "go", Path("/work"))
    assert result.session_id is None
    assert "--session-id" not in cast(list[str], captured["argv"])


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
    assert Path("~/.copilot/session-state") == runner.DEFAULT_COPILOT_SESSION_STORE
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


def test_extract_usage_none_when_nothing_executed() -> None:
    """A handoff or dry run has no transcript to meter: no usage, not a zero estimate."""
    handoff = RunResult(MANUAL_RUNNER, (), executed=False, handoff=True)
    assert runner.extract_usage(RunnerSpec(MANUAL_RUNNER, HANDOFF), handoff) is None
    dry = RunResult("claude", ("claude",), executed=False)
    assert runner.extract_usage(_claude_spec(), dry) is None


def test_builtin_usage_formats_pin_the_probed_capabilities() -> None:
    """Each headless builtin meters through its probed envelope; the handoff has none.

    Copilot's is the odd one: it reports nothing usable on stdout, so it meters
    out of band from its own session store (probed 1.0.75, basicly-2rn9) rather
    than falling back to the transcript estimate the way it used to.
    """
    by_name = {s.name: s.usage_format for s in BUILTIN_RUNNERS}
    assert by_name["claude"] == CLAUDE_STREAM_JSON
    assert by_name["codex"] == CODEX_JSONL
    assert by_name["copilot"] == COPILOT_SESSION_STORE
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
    """Codex occupancy is the last turn's tokens; summing turns is the cost view.

    Deliberately still input + output, not the split: occupancy is what the window
    held, and `input_tokens` already carries the cached portion — re-adding
    `cached_input_tokens` here would double-count *and* measure the wrong quantity.
    """
    last_turn, _total = _CODEX_TURNS[-1]
    occupancy = runner.context_occupancy(_codex_spec(), _executed(_codex_spec(), _CODEX_EVENTS))
    assert occupancy == last_turn["input_tokens"] + last_turn["output_tokens"]
    assert occupancy == 16829


def test_context_occupancy_never_falls_back_to_the_transcript_estimate() -> None:
    """A format with no occupancy view, or a parse miss, yields None — never an estimate.

    Stdout length says nothing about window occupancy, and a false trigger
    would spin a phantom follow-up bead. Copilot is deliberately still None even
    now that it *does* report measured tokens (basicly-2rn9): its store carries a
    real occupancy view, but turning a ceiling on is its own behaviour change and
    wants its own bead — so this pins that the cost meter did not quietly become
    one.
    """
    copilot = next(s for s in BUILTIN_RUNNERS if s.name == "copilot")
    assert copilot.usage_format == COPILOT_SESSION_STORE
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


# Assert against the *source's* portable constant rather than a second definition
# here: on Windows the two fallbacks could differ and the test would compare a
# value the code never produced (basicly-kjc5.54).
SIGKILL = runner.SIGKILL


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


def test_record_dispatch_carries_copilot_measured_usage(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed copilot dispatch records the measured split and credits, not the estimate.

    The AC's measured half, end to end: the store is read through the same
    ``record_dispatch`` every dispatch site calls, so what lands on disk is what
    the D3 ceiling and the rollups will see.
    """
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
    assert entry["cost"] is None  # credits are not USD
    # The recorded command stays redacted, and carries no store key: the session
    # id is dispatch state, not something a metadata-only record needs to keep.
    assert runner.run_record.REDACTED_PROMPT in entry["command"]
    assert "--session-id" not in entry["command"]


def test_record_dispatch_carries_the_codex_measured_split(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A completed codex dispatch records the per-kind split, not just the total.

    End to end through the same ``record_dispatch`` every dispatch site calls, so
    what lands on disk is what the spend forecast will calibrate against
    (basicly-jr0l.37): the cached portion is visible, and ``tokens`` is still the
    single summed total the D3 ceiling and the rollups read.
    """
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
    # Codex bills in neither field the harness can read: no USD, no AI credits.
    assert entry["cost"] is None and entry["credits"] is None


def test_record_dispatch_records_a_missing_copilot_store_as_an_estimate(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The AC's fallback half: an unreadable store is recorded flagged, not as measured."""
    spec = _copilot_spec(tmp_path / "session-state")  # never written
    result = _copilot_run(spec)
    monkeypatch.setattr(runner.run_record, "record_marker", lambda *_a, **_k: None)

    runner.record_dispatch(tmp_path, "basicly-2rn9", spec, result, prompt="p", phase="build")

    (entry,) = (runner.run_record.load_run_records(tmp_path) or {})["basicly-2rn9"]
    assert entry["estimated"] is True
    assert entry["tokens"] == len(result.stdout) // 4
    assert entry["credits"] is None
    assert entry["input_tokens"] is None


# --- Global agent-process budget (component 8, basicly-kjc5.11) ---------------


@pytest.fixture(autouse=True)
def _clean_process_budget():
    """The budget is process-wide, so a test must never inherit another's."""
    runner.reset_process_budget()
    yield
    runner.reset_process_budget()


def test_budget_splits_the_ceiling_into_reservation_classes() -> None:
    """The section-6 split: concurrency for lanes, one for the decider, rest helpers."""
    budget = runner.ProcessBudget(8, 3)
    assert (budget.lane_slots, budget.decider_slots, budget.helper_slots) == (3, 1, 4)
    assert budget.capacity(runner.LANE) == 3
    assert budget.capacity(runner.HELPER) == 4


@pytest.mark.parametrize(
    ("total", "concurrency"),
    [(8, 4), (8, 3), (4, 4), (2, 8), (1, 4), (0, 1), (-5, 1)],
)
def test_budget_reservations_never_overcommit_the_ceiling(total: int, concurrency: int) -> None:
    """Whatever the config says, the classes sum within the ceiling.

    A ceiling below "the decider plus one lane" is raised to that minimum rather
    than overcommitting the machine or leaving zero lane slots, which would refuse
    every dispatch.
    """
    budget = runner.ProcessBudget(total, concurrency)
    assert budget.lane_slots + budget.decider_slots + budget.helper_slots <= budget.total
    assert budget.lane_slots >= 1
    assert budget.decider_slots == runner.DECIDER_SLOTS


def test_budget_keeps_the_decider_slot_when_the_ceiling_is_tight() -> None:
    """A tight ceiling narrows the lanes, never the reservation that unwedges them.

    The decider's slot exists to keep the decision queue workable, and the lanes
    are what wait on those decisions — so dropping it to fit more lanes would
    recreate the deadlock it was reserved to prevent.
    """
    budget = runner.ProcessBudget(4, 8)  # asks for 8 lanes inside a ceiling of 4
    assert budget.decider_slots == 1
    assert budget.lane_slots == 3


def test_budget_helpers_queue_while_lane_and_decider_slots_stay_free() -> None:
    """The acceptance criterion: an exhausted remainder queues helpers, not lanes."""
    budget = runner.ProcessBudget(4, 2)  # lane 2, decider 1, helper 1
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
    # The only helper slot is taken, so the second helper is queued...
    assert not second_entered.wait(0.2)
    # ...while both reserved classes are still immediately available.
    with budget.slot(runner.LANE), budget.slot(runner.DECIDER):
        assert budget.live(runner.LANE) == 1
        assert budget.live(runner.DECIDER) == 1

    release.set()
    assert second_entered.wait(5)  # the queued helper runs once the slot frees
    first.join(5)
    second.join(5)
    assert budget.live(runner.HELPER) == 0


def test_budget_helper_flood_never_blocks_a_lane() -> None:
    """A lane must never wait behind helpers, or the pass can deadlock."""
    budget = runner.ProcessBudget(6, 2)  # lane 2, decider 1, helper 3
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

    # Every helper slot is held; a lane still acquires without waiting.
    with budget.slot(runner.LANE, timeout=1):
        assert budget.live(runner.LANE) == 1

    release.set()
    for thread in threads:
        thread.join(5)


def test_budget_releases_a_slot_when_the_dispatch_raises() -> None:
    """A crashing dispatch must not leak its slot, or the budget bleeds to zero."""
    budget = runner.ProcessBudget(8, 2)
    with pytest.raises(RuntimeError, match="boom"), budget.slot(runner.LANE):
        raise RuntimeError("boom")
    assert budget.live(runner.LANE) == 0


def test_budget_refuses_a_helper_when_no_remainder_exists() -> None:
    """Queueing on a queue that can never drain is a hang; refuse instead (D9)."""
    budget = runner.ProcessBudget(3, 2)  # lane 2, decider 1, helper 0
    assert budget.helper_slots == 0
    with (
        pytest.raises(runner.BudgetExhaustedError, match="max_agent_processes"),
        budget.slot(runner.HELPER),
    ):
        pass


def test_budget_helper_wait_times_out_rather_than_hanging_forever() -> None:
    """An explicit timeout is available for a caller that must not block indefinitely."""
    budget = runner.ProcessBudget(4, 2)  # helper 1
    with (
        budget.slot(runner.HELPER),
        pytest.raises(TimeoutError, match="helper process slot"),
        budget.slot(runner.HELPER, timeout=0.05),
    ):
        pass


def test_budget_rejects_an_unknown_process_class() -> None:
    """The class vocabulary is closed: a typo must not silently go unbudgeted."""
    budget = runner.ProcessBudget(8, 2)
    with pytest.raises(ValueError, match="unknown process class"):
        budget.capacity("vibes")


def test_process_budget_is_configured_once_per_process() -> None:
    """First caller wins: re-deriving the ceiling while slots are held could exceed it."""
    first = runner.configure_process_budget(8, 2)
    again = runner.configure_process_budget(64, 32)
    assert again is first
    assert first.total == 8
    assert runner.process_budget() is first


def test_process_budget_defaults_when_nothing_configured_it() -> None:
    """A single-track session never configures one; accounting still happens."""
    budget = runner.process_budget()
    assert budget.total == runner.DEFAULT_MAX_AGENT_PROCESSES
    # Lane slots follow the design rule of thumb (ceiling is ~2x concurrency), so
    # the fallback cannot drift from the ceiling it is derived from.
    assert budget.lane_slots == budget.total // 2


def test_every_engine_dispatch_site_declares_a_class() -> None:
    """No engine-initiated agent spawn may go unbudgeted.

    Guards the wiring rather than the accounting: a new `runner.run` call site
    that forgets its slot spends machine capacity nothing is counting. The two
    `basicly runner` debugging commands are exempt on purpose — a human running
    one command by hand is not the factory allocating capacity.
    """
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
            # The slot is the enclosing `with`, so look back a few lines for it.
            window = "\n".join(lines[max(0, number - 4) : number + 1])
            if ".slot(runner." not in window:
                unbudgeted.append(f"{path.name}:{number + 1}: {line.strip()}")
    assert not unbudgeted, "unbudgeted agent dispatch site(s): " + "; ".join(unbudgeted)


# --- No engine interval is measured on a wall clock (basicly-jr0l.5) ---------
#
# A wall clock can step backwards — an unconverged NTP resync does it routinely
# — so any duration, timeout or deadline derived from one can come out negative
# or short. Every such measurement in the engine already uses perf_counter or
# monotonic, which are immune by construction; this keeps that a gate rather
# than a convention nobody can see.
#
# The exemptions are the sites where a monotonic reading would be *meaningless*,
# not merely inconvenient: both compare against a value produced outside this
# process, and monotonic clocks share no origin across a reboot or a file's
# mtime. Each is one `_now()` indirection, which is also the tests' clock seam.

WALL_CLOCK_EXEMPT = {
    "supervise.py": "lock staleness subtracts a filesystem mtime, not a reading of ours",
    "policy.py": "the confirm-code TTL is persisted to disk and read back by another process",
}


def test_no_engine_interval_is_measured_on_a_wall_clock() -> None:
    """``time.time()`` appears only in the two exempt ``_now()`` seams, and nowhere else.

    Also bans ``.total_seconds()``, the other way an interval sneaks onto the
    wall clock: subtracting two ``datetime.now()`` readings. Scope is deliberate
    and narrow — this pins where the *clock* comes from, not every arithmetic
    shape — so a violation is always a real one rather than a heuristic to
    suppress.
    """
    src = Path(__file__).resolve().parents[1] / "src" / "basicly"
    offenders: list[str] = []
    seen_exempt: set[str] = set()
    for path in sorted(src.glob("*.py")):
        lines = path.read_text(encoding="utf-8").splitlines()
        for number, line in enumerate(lines):
            if ".total_seconds()" in line:
                offenders.append(f"{path.name}:{number + 1}: duration from a datetime difference")
                continue
            if "time.time()" not in line:
                continue
            if path.name not in WALL_CLOCK_EXEMPT:
                offenders.append(f"{path.name}:{number + 1}: {line.strip()}")
                continue
            # An exemption covers the `_now()` seam it was granted for, not the
            # whole module: anything else in the file is still a violation.
            window = "\n".join(lines[max(0, number - 3) : number + 1])
            if "def _now(" not in window:
                offenders.append(f"{path.name}:{number + 1}: outside the exempt _now() seam")
            else:
                seen_exempt.add(path.name)
    assert not offenders, "wall-clock interval(s) in the engine: " + "; ".join(offenders)
    # Keep the exemption list honest: a site that stopped needing it must be
    # removed, or the next reader treats a dead entry as licence.
    assert seen_exempt == set(WALL_CLOCK_EXEMPT), (
        f"stale wall-clock exemption(s): {sorted(set(WALL_CLOCK_EXEMPT) - seen_exempt)}"
    )


# --- Stall detection (component 6 mechanic, basicly-kjc5.25) ------------------


def _wait_until(predicate: Callable[[], bool], *, timeout: float) -> None:
    """Poll *predicate* until true or *timeout*; keeps timing tests off wall-clock sleeps.

    A fixed sleep long enough for a loaded CI runner would be far too long for a
    laptop, and one short enough for a laptop goes red on CI — this waits for the
    condition instead of for a duration.
    """
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if predicate():
            return
        time.sleep(0.01)


def test_stall_watchdog_flags_an_unchanging_dispatch_exactly_once() -> None:
    """One queue item per wedged lane, not one per poll."""
    fired: list[float] = []
    with runner.StallWatchdog(
        0.05, probe=lambda: "frozen", on_stall=lambda: fired.append(time.monotonic()), poll=0.01
    ):
        # Many windows' worth of headroom: the assertion is "once", not "fast".
        _wait_until(lambda: len(fired) == 1, timeout=10)
        time.sleep(0.3)
    assert len(fired) == 1


def test_stall_watchdog_stays_quiet_while_the_lane_makes_progress() -> None:
    """Any change in the fingerprint restarts the clock, so slow work is not a stall."""
    counter = itertools.count()
    fired: list[int] = []
    with runner.StallWatchdog(
        0.5, probe=lambda: str(next(counter)), on_stall=lambda: fired.append(1), poll=0.01
    ):
        time.sleep(0.3)  # well inside the window, and the probe moves every poll
    assert fired == []


def test_stall_watchdog_flags_a_lane_that_goes_quiet_after_working() -> None:
    """The real shape of a wedge: progress, then nothing."""
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
        time.sleep(0.2)  # inside the window while the probe keeps moving
        assert fired == [], "working lane flagged"
        moving["frozen"] = True
        _wait_until(lambda: watchdog.flagged, timeout=10)
    assert fired == [1]


def test_stall_watchdog_never_lets_a_failing_probe_or_notifier_escape() -> None:
    """It only observes a dispatch; it must never be able to break one."""

    def exploding_probe() -> str:
        raise OSError("worktree vanished")

    def exploding_notifier() -> None:
        raise RuntimeError("tracker down")

    # A probe that always raises reads as unchanged, so it still reaches the
    # notifier — and the notifier's own failure is contained too.
    with runner.StallWatchdog(
        0.05, probe=exploding_probe, on_stall=exploding_notifier, poll=0.01
    ) as watchdog:
        _wait_until(lambda: watchdog.flagged, timeout=10)
    assert watchdog.flagged is True  # it tried, and survived


def test_stall_watchdog_stops_cleanly_before_it_ever_fires() -> None:
    """A dispatch that finishes quickly leaves no watcher thread behind."""
    fired: list[int] = []
    watchdog = runner.StallWatchdog(
        60.0, probe=lambda: "x", on_stall=lambda: fired.append(1), poll=0.02
    )
    with watchdog:
        time.sleep(0.05)
    assert fired == []
    assert watchdog.flagged is False

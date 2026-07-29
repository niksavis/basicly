"""Agent-agnostic runner adapters (onb.7).

A *runner* is the thin adapter that invokes a coding agent headless to do a
node's work in its worktree (architecture §12.8): an invocation command, its
headless flags, how the prompt is injected, and how output is captured. The loop
logic is agent-neutral; only the runner differs per agent, so the same loop runs
identically under Claude, Codex, or Copilot.

Two kinds:

- ``headless`` — a known CLI (claude/codex/copilot, or any agent added via
  config) invoked non-interactively with the prompt injected as an argument or
  on stdin, output captured.
- ``handoff`` — the safe fallback. There is no cross-agent CLI invocation
  standard, so this runner **never guesses** an unknown agent's command line.
  When no known CLI is on PATH and none is configured, it degrades to the loop's
  block-and-resume contract: it surfaces the exact prompt + worktree path and
  leaves the work to whoever is driving (the current agent or a human), who then
  re-invokes. That leans on the two things that *are* standardized — the
  projected AGENTS.md guidance and the tracker-backed resumability.

Command templates are config-driven with the built-in defaults below; verify any
one before a live run with ``basicly runner dry-run``.
"""

from __future__ import annotations

import contextlib
import hashlib
import json
import os
import shutil
import signal
import subprocess
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass, replace
from pathlib import Path

from . import run_record
from .redact import redact_secrets

# Marker replaced by the prompt when a runner injects it as a command argument.
PROMPT_PLACEHOLDER = "{prompt}"

# Marker replaced by the pinned model, the escape hatch for an agent whose
# model flag is not `--model` (see format_command). Optional in a command.
MODEL_PLACEHOLDER = "{model}"

# Runner kinds.
HEADLESS = "headless"
HANDOFF = "handoff"

# Flag a headless binary is probed with to confirm its assumed capabilities
# (basicly-bveo) without doing any work.
HELP_FLAG = "--help"

# How the prompt reaches the agent.
PROMPT_VIA = ("arg", "stdin")

# How a family spells a tool denial on its argv (RunnerSpec.deny_style).
# `--deny-tool=<spec>` once per entry (copilot, verified against its --help),
# versus a single `--disallowedTools` taking every name after it (claude).
DENY_TOOL_FLAG = "deny-tool"
DISALLOWED_TOOLS_FLAG = "disallowed-tools"
DENY_STYLES = (DENY_TOOL_FLAG, DISALLOWED_TOOLS_FLAG)

# The name of the built-in handoff fallback runner.
MANUAL_RUNNER = "manual"

# Detection order for ``auto``: the big 3 by PATH, then the handoff fallback.
AUTO = "auto"
AUTO_ORDER = ("claude", "codex", "copilot")

# Usage-report formats a headless CLI can emit (basicly-kjc5.1): how a
# usage-capturing dispatch asks the CLI to report token usage and how
# extract_usage parses the captured output. None means the CLI reports no
# usage, so the chars/4 transcript estimate applies.
CLAUDE_JSON = "claude-json"  # `--output-format json`: one result object with a usage block
# `--output-format stream-json --verbose`: JSONL events, one per turn, ending in
# the same result object. The only claude envelope that carries *per-turn* usage,
# which is what the context-ceiling meter needs (basicly-kjc5.14).
CLAUDE_STREAM_JSON = "claude-stream-json"
CODEX_JSONL = "codex-jsonl"  # `--json`: JSONL event stream with turn.completed usage
USAGE_FORMATS = (CLAUDE_JSON, CLAUDE_STREAM_JSON, CODEX_JSONL)

# Context-window defaults per adapter (factory design §6, basicly-kjc5.6): the
# denominator for the context-ceiling meter. Conservative published windows;
# config-overridable per agent via `context_window`. Unknown agents get the
# smallest of the big 3 so the ceiling errs toward finalizing early, never late.
DEFAULT_CONTEXT_WINDOW = 128_000
_CONTEXT_WINDOWS = {"claude": 200_000, "codex": 400_000, "copilot": 128_000}

# Flags appended for a usage-capturing dispatch. Trailing — after the prompt —
# so a subcommand invocation like `codex exec` keeps the flag inside the
# subcommand; both CLIs accept options after positional arguments. Kept out of
# spec.command so the --help capability probe is untouched (same stance as
# sandbox/approval).
_USAGE_FLAGS = {
    CLAUDE_JSON: ("--output-format", "json"),
    # claude refuses stream-json under -p without --verbose.
    CLAUDE_STREAM_JSON: ("--output-format", "stream-json", "--verbose"),
    CODEX_JSONL: ("--json",),
}


@dataclass(frozen=True)
class RunnerSpec:
    """One agent adapter: how to invoke it headless (or that it is a handoff)."""

    name: str
    kind: str = HEADLESS
    # For a headless runner: the argv template. When prompt_via == "arg" it must
    # contain exactly one PROMPT_PLACEHOLDER element. Empty for a handoff runner.
    command: tuple[str, ...] = ()
    prompt_via: str = "arg"
    # Optional pinned model, folded into the command by format_command: a
    # `{model}` placeholder is substituted, otherwise `--model <value>` is
    # injected right after the binary. None leaves the argv unchanged.
    model: str | None = None
    # Invocation-time tool-deny specs (basicly-lqz5). format_command emits them
    # after the binary in this family's `deny_style` wire form. Populated for the
    # copilot runner from permissions.yaml at config load, and for any family by
    # confine_for_decider; empty leaves the argv unchanged.
    deny_tools: tuple[str, ...] = ()
    # How this family spells a tool denial on its argv (basicly-kjc5.16). The
    # flag shapes differ — copilot takes one `--deny-tool=<spec>` per entry,
    # claude takes a single `--disallowedTools` with the names after it — so the
    # style travels with the spec and `deny_tools` stays the family's own
    # vocabulary. None means the family has no known tool-deny flag (codex
    # confines with its sandbox instead), and `deny_tools` is then unusable.
    deny_style: str | None = None
    # Invocation-time sandbox/approval guardrails (basicly-t0kt). Codex forbids
    # overriding approval_policy/sandbox_mode at repo scope in .codex/config.toml
    # by design, so safe defaults cannot be projected as committed catalog output;
    # the only seam is the invocation. format_command emits `--sandbox <mode>` and
    # `-a <policy>` after the binary when set. Defaulted for the codex runner
    # (`workspace-write` disables network by default; `on-failure` fails safe in
    # headless exec — no approver, so an escalation is denied, not auto-granted).
    # None on claude/copilot leaves their argv unchanged.
    sandbox: str | None = None
    approval: str | None = None
    # Optional opt-in per-agent bot git identity (basicly-smzg). When both are
    # set, run() injects GIT_AUTHOR_*/GIT_COMMITTER_* into the dispatched agent's
    # environment so commits it makes in its worktree carry the bot identity
    # (still subject to the identity-guard pre-commit gate — a bot email must
    # satisfy basicly.identityAllowEmail when strict mode is on). Both or neither:
    # the config parser rejects a lone half.
    git_name: str | None = None
    git_email: str | None = None
    # Usage-report format for token telemetry (basicly-kjc5.1), one of
    # USAGE_FORMATS or None. None — the CLI reports no token usage (copilot,
    # probed 2026-07-22: its result event carries premium-request counts, not
    # tokens) — makes a usage-capturing dispatch fall back to the chars/4
    # transcript estimate (design 7.5).
    usage_format: str | None = None
    # The model's context window in tokens (basicly-kjc5.6): the denominator for
    # the [policy.sizing] context_ceiling meter (design D8). Per-adapter defaults
    # in _CONTEXT_WINDOWS; config-overridable per agent.
    context_window: int = DEFAULT_CONTEXT_WINDOW

    @property
    def binary(self) -> str | None:
        """The executable this runner shells out to, or None for a handoff."""
        return self.command[0] if self.command else None


# Built-in adapters. The big-3 command templates are best-effort defaults;
# they are config-overridable and every one is printable via `runner dry-run`.
BUILTIN_RUNNERS: tuple[RunnerSpec, ...] = (
    RunnerSpec(
        "claude",
        HEADLESS,
        ("claude", "-p", PROMPT_PLACEHOLDER),
        deny_style=DISALLOWED_TOOLS_FLAG,
        usage_format=CLAUDE_STREAM_JSON,
        context_window=_CONTEXT_WINDOWS["claude"],
    ),
    RunnerSpec(
        "codex",
        HEADLESS,
        ("codex", "exec", PROMPT_PLACEHOLDER),
        sandbox="workspace-write",
        approval="on-failure",
        usage_format=CODEX_JSONL,
        context_window=_CONTEXT_WINDOWS["codex"],
    ),
    RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        deny_style=DENY_TOOL_FLAG,
        context_window=_CONTEXT_WINDOWS["copilot"],
    ),
    RunnerSpec(MANUAL_RUNNER, HANDOFF),
)


@dataclass(frozen=True)
class RunResult:
    """The outcome of a (possibly dry or handed-off) runner invocation."""

    runner: str
    command: tuple[str, ...]
    executed: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    handoff: bool = False
    # Wall-clock seconds around the subprocess; None when nothing executed
    # (a handoff or a dry run). Feeds the loop's run-record (basicly-z6dh).
    duration_s: float | None = None
    # The dispatch hit [runner] runner_timeout and was hard-killed
    # (basicly-kjc5.7, design section 6): the supervisor routes this to the
    # decision queue as a stall flag. returncode is None on a timeout.
    timed_out: bool = False


def format_command(spec: RunnerSpec, prompt: str, *, capture_usage: bool = False) -> list[str]:
    """Return the exact argv *spec* would execute for *prompt*.

    Prompt injection is unchanged: an ``arg`` runner substitutes its
    ``{prompt}`` placeholder, a ``stdin`` runner takes the command verbatim.

    Model pinning (basicly-45ld) layers on top when ``spec.model`` is set: a
    ``{model}`` placeholder in the command is substituted with the model (the
    escape hatch for an agent whose flag is not ``--model``); otherwise
    ``--model <value>`` is injected immediately after the binary. With no model
    set the argv is unchanged — and a ``{model}`` placeholder with no model to
    fill it is a config error, raised rather than left literal in the argv
    (symmetric to the missing-prompt-placeholder guard below).

    Sandbox/approval guardrails (basicly-t0kt) layer on the same way: when
    ``spec.sandbox``/``spec.approval`` are set, ``--sandbox <mode>`` and
    ``-a <policy>`` are injected after the binary (the codex runner defaults
    them). Unset leaves the argv unchanged.

    *capture_usage* (basicly-kjc5.1) appends the spec's usage-report flags so
    the CLI emits token usage for :func:`extract_usage`. Opt-in per call site
    because it changes the output shape (claude's stdout becomes one JSON
    object): the loop's run-record dispatch captures usage; consumers that
    parse the agent's answer as plain text (rubric judging, catalog review)
    must not set it.

    Raises for a handoff runner (it has no command line) and for an arg-injected
    template missing its prompt placeholder — a silent drop would send an empty
    prompt.
    """
    if spec.kind != HEADLESS:
        raise ValueError(f"runner {spec.name!r} is {spec.kind}, not headless; it has no command")
    if spec.prompt_via == "arg":
        if PROMPT_PLACEHOLDER not in spec.command:
            raise ValueError(
                f"runner {spec.name!r} injects the prompt as an argument but its command "
                f"has no {PROMPT_PLACEHOLDER!r} placeholder"
            )
        argv = [prompt if part == PROMPT_PLACEHOLDER else part for part in spec.command]
    else:
        argv = list(spec.command)
    # Model outermost so it stays "right after the binary" (its documented
    # contract); sandbox/approval and deny-tool flags then follow the model.
    argv = _apply_model(spec, _apply_sandbox(spec, _apply_deny_tools(spec, argv)))
    return _apply_usage(spec, argv) if capture_usage else argv


def _apply_usage(spec: RunnerSpec, argv: list[str]) -> list[str]:
    """Append the usage-report flags for a usage-capturing dispatch (basicly-kjc5.1).

    No format leaves the argv unchanged — the dispatch still runs, and
    :func:`extract_usage` falls back to the transcript estimate. An unknown
    format raises: the config parser validates the value, so this is reachable
    only from a hand-built spec.
    """
    if spec.usage_format is None:
        return argv
    flags = _USAGE_FLAGS.get(spec.usage_format)
    if flags is None:
        raise ValueError(
            f"runner {spec.name!r} has unknown usage_format {spec.usage_format!r}; "
            f"known: {list(USAGE_FORMATS)}"
        )
    return [*argv, *flags]


def _apply_model(spec: RunnerSpec, argv: list[str]) -> list[str]:
    """Fold the pinned model into *argv* (semantics documented on format_command)."""
    has_placeholder = MODEL_PLACEHOLDER in argv
    if spec.model is None:
        if has_placeholder:
            raise ValueError(
                f"runner {spec.name!r} command has a {MODEL_PLACEHOLDER!r} placeholder "
                "but no model is set to fill it"
            )
        return argv
    if has_placeholder:
        return [spec.model if part == MODEL_PLACEHOLDER else part for part in argv]
    return [argv[0], "--model", spec.model, *argv[1:]]


def _apply_deny_tools(spec: RunnerSpec, argv: list[str]) -> list[str]:
    """Inject the family's tool-deny flags after the binary (basicly-lqz5).

    Empty ``deny_tools`` leaves the argv unchanged. ``deny_style`` picks the wire
    form (basicly-kjc5.16): copilot's ``--deny-tool=<spec>`` is emitted once per
    entry, and the ``=`` (single token) form is used so a spec containing
    spaces — e.g. ``shell(git push --force)`` — stays one argv element and is
    never mis-parsed as the next flag; claude's ``--disallowedTools`` takes every
    name as a following argument instead. Denials with no style would be silently
    dropped onto an argv the binary cannot read, so that raises.
    """
    if not spec.deny_tools:
        return argv
    if spec.deny_style == DENY_TOOL_FLAG:
        flags = [f"--deny-tool={tool}" for tool in spec.deny_tools]
    elif spec.deny_style == DISALLOWED_TOOLS_FLAG:
        flags = ["--disallowedTools", *spec.deny_tools]
    else:
        raise ValueError(
            f"runner {spec.name!r} sets deny_tools but has deny_style "
            f"{spec.deny_style!r}; known: {list(DENY_STYLES)}"
        )
    return [argv[0], *flags, *argv[1:]]


def _apply_sandbox(spec: RunnerSpec, argv: list[str]) -> list[str]:
    """Inject sandbox/approval guardrail flags after the binary (basicly-t0kt).

    Emits ``--sandbox <mode>`` and/or ``-a <policy>`` when the spec sets them
    (the codex runner defaults both). Kept out of ``spec.command`` on purpose:
    the values are not headless-capability flags, so folding them here — like
    ``_apply_model`` — leaves the ``--help`` probe (:func:`_headless_flags`)
    untouched. Neither set leaves the argv unchanged.
    """
    flags: list[str] = []
    if spec.sandbox is not None:
        flags += ["--sandbox", spec.sandbox]
    if spec.approval is not None:
        flags += ["-a", spec.approval]
    if not flags:
        return argv
    return [argv[0], *flags, *argv[1:]]


# --- Decider confinement (basicly-kjc5.16, design 7.1) -----------------------
#
# The decider resolves one queued decision from the intake corpus already
# embedded in its prompt (decisions.decider_prompt), so it needs no tools at
# all. Every tool it could reach is a way around its documented contract: a
# shell or write tool lets it record tracker state directly, bypassing
# decider_max_decisions and the abstain path, and a file-read tool lets it
# answer from outside the corpus it is bounded to.
#
# Enumerated per family because the vocabularies and the flag shapes differ, and
# neither claude nor copilot documents a deny-all wildcard. So claude and copilot
# get a blocklist over the tool surface as verified 2026-07-25 — a mitigation of
# the same kind as the policy tripwires, not a boundary, and a tool added to
# either CLI later is allowed until it is listed here. Codex has no tool-deny
# flag but does have a real one: its read-only sandbox cannot write at all.
#
# A family with no known confinement is never dispatched — invoke_decider
# abstains to the human rather than run an unconfined agent (D3's drop-to-human
# stance). That is why this returns None instead of the spec unchanged.

# Claude's built-in tool surface: read, write, execute, delegate, and network.
_DECIDER_DENY_CLAUDE: tuple[str, ...] = (
    "Bash",
    "BashOutput",
    "KillShell",
    "Edit",
    "Write",
    "NotebookEdit",
    "Read",
    "Glob",
    "Grep",
    "Task",
    "TodoWrite",
    "WebFetch",
    "WebSearch",
)
# Every name above is one claude accepts: it warns on stderr for a name matching
# no known tool ("SlashCommand" did, and was dropped after a live probe on
# 2026-07-25), so a typo here would silently leave that tool allowed.

# Copilot names one class per surface, reads included. ``read`` is undocumented
# as a deny name but a live probe on 2026-07-26 established it is accepted and
# enforced — the dispatch returns the CANNOT_READ sentinel — and that the earlier
# shell+write pair did not bound the corpus at all: denying only the shell left
# copilot falling back to its native read tool and reading a canary file outside
# the prompt. ``--available-tools=`` is documented as a whitelist ("Only these
# tools will be available"), but an empty value was ignored rather than denying
# everything, so a blocklist stays the mechanism (basicly-jr0l.27).
_DECIDER_DENY_COPILOT: tuple[str, ...] = ("shell", "write", "read")


def confine_for_decider(spec: RunnerSpec) -> RunnerSpec | None:
    """The decider-confined form of *spec*, or None when this family cannot be confined.

    Applies the family's most restrictive invocation-time overlay: a tool
    blocklist where the CLI has one, and codex's ``read-only`` sandbox with
    approvals off — headless exec has no approver, so ``never`` fails closed on
    an escalation instead of waiting for one. A handoff runner is returned
    unchanged: it has no argv to carry flags and executes nothing.

    The blocklist is added to whatever the spec already denies, never substituted
    for it: copilot's builtin carries the permissions.yaml baseline, which may
    name a class this overlay does not, and confinement must only ever subtract
    capability.
    """
    if spec.kind == HANDOFF:
        return spec
    if spec.deny_style == DISALLOWED_TOOLS_FLAG:
        return replace(spec, deny_tools=_denied_with(spec, _DECIDER_DENY_CLAUDE))
    if spec.deny_style == DENY_TOOL_FLAG:
        return replace(spec, deny_tools=_denied_with(spec, _DECIDER_DENY_COPILOT))
    if spec.sandbox is not None:
        return replace(spec, sandbox="read-only", approval="never")
    return None


def _denied_with(spec: RunnerSpec, extra: tuple[str, ...]) -> tuple[str, ...]:
    """The spec's denials plus *extra*, order-stable and without duplicates."""
    return (*spec.deny_tools, *(t for t in extra if t not in spec.deny_tools))


def is_available(spec: RunnerSpec, *, which: Callable[[str], str | None] | None = None) -> bool:
    """True when this runner can be used: handoff always; headless if its binary is on PATH."""
    which = which or shutil.which
    if spec.kind == HANDOFF:
        return True
    return spec.binary is not None and which(spec.binary) is not None


@dataclass(frozen=True)
class Capability:
    """Whether a headless runner's binary confirms its assumed flag (basicly-bveo)."""

    reachable: bool  # the binary ran when probed with --help
    flag_ok: bool  # the headless flag is present, or the probe could not disprove it
    detail: str


def _headless_flags(spec: RunnerSpec) -> list[str]:
    """The static headless-flag tokens in *spec*'s command (binary + placeholders removed)."""
    return [t for t in spec.command[1:] if t not in (PROMPT_PLACEHOLDER, MODEL_PLACEHOLDER)]


def _run_help(binary: str) -> str | None:
    """Run ``<binary> --help``; return its combined output, or None if it could not run."""
    try:
        proc = subprocess.run(  # nosec B603
            [binary, HELP_FLAG], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.SubprocessError:
        return None
    return (proc.stdout or "") + (proc.stderr or "")


def probe_capability(
    spec: RunnerSpec, *, run: Callable[[str], str | None] | None = None
) -> Capability:
    """Confirm *spec*'s assumed headless flag by probing its binary with ``--help``.

    ``flag_ok`` is False only on *positive* evidence — the probe ran and a flag
    token is absent from the help output (the dropped/renamed-flag case this
    guards). A handoff runner, a spec with no binary, or a probe that could not
    run assumes capable, so a flaky or slow probe never false-skips a working
    agent; PATH presence (:func:`is_available`) stays the primary signal.
    """
    if spec.kind != HEADLESS or spec.binary is None:
        return Capability(reachable=True, flag_ok=True, detail="handoff; no probe needed")
    run = run or _run_help
    out = run(spec.binary)
    if out is None:
        return Capability(
            reachable=False, flag_ok=True, detail=f"could not run {spec.binary} {HELP_FLAG}"
        )
    flags = _headless_flags(spec)
    missing = [flag for flag in flags if flag not in out]
    if missing:
        return Capability(
            reachable=True,
            flag_ok=False,
            detail=f"{spec.binary} {HELP_FLAG} does not mention {', '.join(missing)}",
        )
    supported = ", ".join(flags) or "(none)"
    return Capability(reachable=True, flag_ok=True, detail=f"{spec.binary} supports {supported}")


def is_capable(
    spec: RunnerSpec,
    *,
    which: Callable[[str], str | None] | None = None,
    run: Callable[[str], str | None] | None = None,
) -> bool:
    """True when *spec* is both on PATH and its assumed headless flag is confirmed."""
    return is_available(spec, which=which) and probe_capability(spec, run=run).flag_ok


def select_runner(
    specs: tuple[RunnerSpec, ...],
    chosen: str | None = None,
    *,
    which: Callable[[str], str | None] | None = None,
    capable: Callable[[RunnerSpec], bool] | None = None,
) -> RunnerSpec:
    """Resolve which runner to use.

    An explicit name wins (error if unknown); ``auto`` (or no choice) detects the
    big 3 on PATH in :data:`AUTO_ORDER` and otherwise falls back to the handoff
    runner — an unknown agent's command line is never guessed.

    When *capable* is given (basicly-bveo), ``auto`` skips a runner that is on
    PATH but whose capability probe fails, so a binary with a dropped/renamed
    headless flag is not auto-selected — it falls through to the next candidate
    and finally the manual handoff. With no predicate, selection is PATH-only.
    An explicit choice is never probe-gated (the caller asked for it by name).
    """
    which = which or shutil.which
    by_name = {spec.name: spec for spec in specs}
    if chosen is not None and chosen != AUTO:
        spec = by_name.get(chosen)
        if spec is None:
            raise ValueError(f"unknown runner {chosen!r}; known: {sorted(by_name)}")
        return spec
    for name in AUTO_ORDER:
        spec = by_name.get(name)
        if spec is None:
            continue
        if capable(spec) if capable is not None else is_available(spec, which=which):
            return spec
    fallback = by_name.get(MANUAL_RUNNER)
    if fallback is None:
        raise RuntimeError("no runner detected on PATH and no manual handoff runner configured")
    return fallback


def git_identity_env(spec: RunnerSpec) -> dict[str, str] | None:
    """The GIT_AUTHOR/COMMITTER overrides for *spec*'s bot identity, or None.

    Returns None when the spec carries no bot identity, so the dispatched child
    inherits the environment unchanged (the current, default behavior). When set,
    both name and email are present (the config parser enforces the pairing) and
    all four git identity vars are pinned, so a commit the agent makes reads as
    the bot for both author and committer. This does not bypass identity-guard:
    the bot email must still satisfy basicly.identityAllowEmail when strict mode
    is configured (basicly-smzg).
    """
    if spec.git_name is None or spec.git_email is None:
        return None
    return {
        "GIT_AUTHOR_NAME": spec.git_name,
        "GIT_AUTHOR_EMAIL": spec.git_email,
        "GIT_COMMITTER_NAME": spec.git_name,
        "GIT_COMMITTER_EMAIL": spec.git_email,
    }


def br_attribution_env(spec: RunnerSpec) -> dict[str, str]:
    """The BR_* attribution overlay for a dispatched agent (basicly-kjc5.3, D3).

    br's tier-1 attribution env vars, so every tracker write the dispatched
    agent makes (comments, gates, created beads) is attributed to the agent —
    the audit trail delegated decisions under an autonomy grant rely on.
    ``BR_MODEL`` is set only when the spec pins a model.
    """
    env = {"BR_AGENT_NAME": spec.name, "BR_HARNESS": "basicly-loop"}
    if spec.model is not None:
        env["BR_MODEL"] = spec.model
    return env


# --- Timeout kill: the dispatch's whole tree, portably (basicly-kjc5.15) ------

# Grace between the tree's terminate and its hard kill, and the ceiling on
# draining a killed dispatch's pipes. Fixed semantics, not config: long enough
# for an agent's children to release a worktree lock, short enough that a
# stalled pass is not held up by them.
KILL_GRACE_S = 5.0

# subprocess exposes this flag only on Windows, so a POSIX interpreter cannot
# name the attribute at all; the fallback is the documented CreateProcess value.
CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

# The mirror image: signal exposes SIGKILL only on POSIX. The Windows branch of
# _kill_tree returns before reaching it, so the value is never *used* there — but
# naming the attribute is enough to raise, and a test that fakes ``os.name`` to
# exercise the POSIX branch does reach this line on Windows (basicly-kjc5.54).
# 9 is SIGKILL's universal number; it is a placeholder that keeps the module
# referenceable on any platform, not a signal Windows could deliver.
SIGKILL = getattr(signal, "SIGKILL", 9)


def _kill_tree(proc: subprocess.Popen[str]) -> None:
    """Kill the timed-out dispatch *and every process it spawned*.

    ``Popen.kill`` signals only the direct child, so an agent CLI's own tree —
    test runs, shells, MCP servers — survives the timeout and keeps mutating the
    lane's worktree after the stall was already queued for a human
    (basicly-kjc5.15). Terminate first so children can release what they hold,
    then hard-kill whatever is still standing after :data:`KILL_GRACE_S`.

    Best-effort by construction: a process that exited between the timeout and
    the signal is not an error, and neither is a Windows box without
    ``taskkill`` — the dispatch itself is already being abandoned.
    """
    if os.name == "nt":
        _taskkill_tree(proc.pid)
        return
    for signum in (signal.SIGTERM, SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), signum)
        except OSError:
            return  # already gone, or never had a group of its own
        if signum == SIGKILL:
            return
        try:
            proc.wait(timeout=KILL_GRACE_S)
            return  # the group went down on the polite signal
        except subprocess.TimeoutExpired:
            continue


def _taskkill_tree(pid: int) -> None:
    """Windows tree kill: ``taskkill /T`` walks the child chain from *pid*."""
    try:
        subprocess.run(  # nosec B603 B607 — fixed argv, no shell, system tool
            ["taskkill", "/F", "/T", "/PID", str(pid)],
            capture_output=True,
            check=False,
            timeout=KILL_GRACE_S,
        )
    except OSError, subprocess.SubprocessError:
        return


def _drain(proc: subprocess.Popen[str]) -> tuple[str, str]:
    """Collect whatever a killed dispatch had buffered, without hanging on it.

    ``communicate`` raises before returning output on a timeout, so the output
    is read here — after the tree is down, which is what makes the read finite.
    A descendant still holding the pipe open past the grace is abandoned rather
    than waited on: the stall is already being routed.
    """
    try:
        return proc.communicate(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired, ValueError:
        return "", ""


def run(  # noqa: PLR0913 — mirrors the CLI surface
    spec: RunnerSpec,
    prompt: str,
    cwd: Path,
    *,
    dry_run: bool = False,
    capture_usage: bool = False,
    timeout: float | None = None,
) -> RunResult:
    """Invoke *spec* on *prompt* in *cwd*, capturing output.

    A handoff runner never executes — it returns a handoff result so the caller
    surfaces the prompt and leaves the work to the driving agent/human. A dry run
    returns the exact argv without executing it. *capture_usage* asks the CLI to
    report token usage (see :func:`format_command`); parse the result with
    :func:`extract_usage`. *timeout* hard-kills the dispatch after that many
    seconds (basicly-kjc5.7): the result comes back ``timed_out`` with whatever
    output was captured, so the caller can route the stall instead of hanging.
    The kill takes the dispatch's **whole process tree** with it — see
    :func:`_kill_tree`; an agent CLI's children must not outlive the stall that
    was queued for it (basicly-kjc5.15).
    """
    if spec.kind == HANDOFF:
        return RunResult(spec.name, (), executed=False, handoff=True)
    argv = format_command(spec, prompt, capture_usage=capture_usage)
    if dry_run:
        return RunResult(spec.name, tuple(argv), executed=False)
    stdin = prompt if spec.prompt_via == "stdin" else None
    # An arg-prompt dispatch must get stdin *closed*, not inherited (basicly-jr0l.36).
    # Popen's stdin=None means inherit, and an agent CLI that reads stdin for extra
    # context then blocks on the supervisor's own stdin until the dispatch timeout —
    # codex exec does exactly this ("Reading additional input from stdin..."), which
    # reads as a wedged lane to the StallWatchdog rather than as the hang it is. The
    # prompt is already on the argv, so there is nothing this end should ever send.
    stdin_source = subprocess.PIPE if stdin is not None else subprocess.DEVNULL
    # Overlay br attribution (basicly-kjc5.3) and, when configured, the bot git
    # identity (basicly-smzg) on the inherited environment.
    identity = git_identity_env(spec)
    env = {**os.environ, **br_attribution_env(spec), **(identity or {})}
    start = time.perf_counter()
    timed_out = False
    # Popen, not subprocess.run: run's timeout kills only the direct child, and
    # the dispatch must be started in its own process group to be killable as a
    # tree at all (basicly-kjc5.15). POSIX gets a new session, whose id is the
    # child's pid — that is what lets os.killpg reach every descendant. Windows
    # has no equivalent for signalling a tree (taskkill /T walks it instead); it
    # gets its own group only so a stray Ctrl-C cannot cross over. Each flag is
    # inert on the other platform.
    proc = subprocess.Popen(  # nosec B603
        argv,
        cwd=cwd,
        stdin=stdin_source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
        start_new_session=os.name != "nt",
        creationflags=CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
        returncode: int | None = proc.returncode
    except subprocess.TimeoutExpired:
        timed_out = True
        returncode = None
        _kill_tree(proc)
        stdout, stderr = _drain(proc)
    except KeyboardInterrupt:
        # Its own session means the dispatch no longer shares the terminal's
        # signals, so an interrupted operator would otherwise leave the agent and
        # its children running against the worktree. Take the tree down here
        # instead, then let the interrupt propagate.
        _kill_tree(proc)
        _drain(proc)
        raise
    duration_s = time.perf_counter() - start
    # Redact secrets at the source so no downstream surface (CLI print, loop log)
    # can leak a credential the agent echoed (basicly-3p2i). Network egress is not
    # sandboxed here — that is agent-layer (codex basicly-t0kt, claude/copilot
    # config); basicly cannot portably restrict a generic subprocess.
    return RunResult(
        spec.name,
        tuple(argv),
        executed=True,
        returncode=returncode,
        stdout=redact_secrets(stdout),
        stderr=redact_secrets(stderr),
        duration_s=duration_s,
        timed_out=timed_out,
    )


@dataclass(frozen=True)
class Usage:
    """Token usage for one executed run: adapter-reported, or a chars/4 estimate."""

    tokens: int
    cost: float | None
    estimated: bool


# Claude usage-block keys: input_tokens excludes the cache fields (Anthropic
# usage semantics), so the total processed is the sum of all four.
_CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
# Codex usage keys: cached_input_tokens is a subset of input_tokens (OpenAI
# usage semantics), so adding it would double-count.
_CODEX_TOKEN_KEYS = ("input_tokens", "output_tokens")


def extract_usage(spec: RunnerSpec, result: RunResult) -> Usage | None:
    """Token usage for *result*: adapter-reported when parseable, else estimated.

    None when nothing executed (a handoff or a dry run) — there is no transcript
    to meter. A spec whose format is None (the CLI reports no usage) or output
    that does not parse falls back to a chars/4 estimate over the captured
    transcript, flagged ``estimated`` so calibration can down-weight it
    (design 7.5).
    """
    if not result.executed:
        return None
    reported: Usage | None = None
    if spec.usage_format == CLAUDE_JSON:
        reported = _claude_json_usage(result.stdout)
    elif spec.usage_format == CLAUDE_STREAM_JSON:
        reported = _claude_json_usage(_claude_result_event(result.stdout))
    elif spec.usage_format == CODEX_JSONL:
        reported = _codex_jsonl_usage(result.stdout)
    if reported is not None:
        return reported
    return Usage(tokens=(len(result.stdout) + len(result.stderr)) // 4, cost=None, estimated=True)


def _claude_json_usage(stdout: str) -> Usage | None:
    """Parse claude's ``--output-format json`` result object (one JSON object).

    Tokens sum the usage block's input/output/cache fields; cost comes from
    ``total_cost_usd``. None on any parse miss so the caller falls back to the
    estimate.
    """
    try:
        obj = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    if not isinstance(obj, dict) or not isinstance(obj.get("usage"), dict):
        return None
    usage = obj["usage"]
    values = [usage[key] for key in _CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int)]
    if not values:
        return None
    cost = obj.get("total_cost_usd")
    return Usage(
        tokens=sum(values),
        cost=float(cost) if isinstance(cost, int | float) else None,
        estimated=False,
    )


def _claude_stream_events(stdout: str) -> list[dict]:
    """The parseable JSON objects in a claude ``stream-json`` transcript, in order.

    Unparseable lines are skipped rather than failing the whole read: the stream
    is interleaved with whatever the CLI writes around it, and a truncated final
    line is normal for a killed dispatch.
    """
    events: list[dict] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            obj = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            events.append(obj)
    return events


def _claude_result_event(stdout: str) -> str:
    """The stream's terminating ``result`` event, re-serialized, or an empty string.

    Lets the cumulative cost/token view reuse :func:`_claude_json_usage`: the
    stream ends in the very same result object the non-streaming envelope emits,
    so there is one parser for it and no second definition of "total".
    """
    for event in reversed(_claude_stream_events(stdout)):
        if event.get("type") == "result":
            return json.dumps(event)
    return ""


def _claude_last_turn_usage(stdout: str) -> dict | None:
    """The usage block of the stream's **last assistant message**.

    That is the occupancy view (design D8): what the window held on the final
    call. The cumulative result-event sum is not — ``cache_read_input_tokens``
    re-counts the context every turn, so it exceeds the window on any healthy
    multi-turn run.
    """
    for event in reversed(_claude_stream_events(stdout)):
        if event.get("type") != "assistant":
            continue
        message = event.get("message")
        if isinstance(message, dict) and isinstance(message.get("usage"), dict):
            return message["usage"]
    return None


def _codex_jsonl_usage(stdout: str) -> Usage | None:
    """Sum token usage over codex's ``--json`` event stream (JSONL).

    Each ``turn.completed`` event carries a usage object; input and output
    tokens sum across turns. Codex reports no cost. None when no usage event
    parses, so the caller falls back to the estimate.
    """
    total = 0
    found = False
    for usage in _codex_turn_usages(stdout):
        values = [usage[key] for key in _CODEX_TOKEN_KEYS if isinstance(usage.get(key), int)]
        if values:
            total += sum(values)
            found = True
    return Usage(tokens=total, cost=None, estimated=False) if found else None


def _codex_turn_usages(stdout: str) -> list[dict]:
    """The usage objects of codex's ``turn.completed`` events, in stream order."""
    usages: list[dict] = []
    for line in stdout.splitlines():
        stripped = line.strip()
        if not stripped:
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != "turn.completed":
            continue
        usage = event.get("usage")
        if isinstance(usage, dict):
            usages.append(usage)
    return usages


def context_occupancy(spec: RunnerSpec, result: RunResult) -> int | None:
    """The run's final context occupancy in tokens, or None when unknowable.

    The numerator for the context-ceiling meter (basicly-kjc5.6, design D8):
    how full the model's window was at the *end* of the run — distinct from
    :func:`extract_usage`, which totals processing for cost telemetry.

    - ``claude-stream-json``: the **last assistant message's** usage. Each
      streamed turn reports the window it actually worked in, so the final one
      is the occupancy (basicly-kjc5.14).
    - ``claude-json``: **None.** Probed 2026-07-23: the result object's usage
      block is session-cumulative — ``cache_read_input_tokens`` re-counts the
      context every turn (a 2-turn run reported ~43K against a ~24K final
      context), so the sum would cross any ceiling on every healthy multi-turn
      run; and its ``iterations`` array omits the final call, so the last-turn
      view is not recoverable from this envelope. This is why the built-in
      claude adapter meters through the streaming format instead; a consumer who
      pins ``claude-json`` keeps exact cost telemetry and an inert ceiling.
    - ``codex-jsonl``: the **last** ``turn.completed`` usage; its
      ``input_tokens`` already carry the whole conversation re-sent that turn,
      so summing across turns (the cost view) would overstate occupancy.
    - No usage format, nothing executed, or a parse miss: None. The chars/4
      transcript estimate is deliberately *not* used here — stdout length says
      nothing about window occupancy, and a false ceiling trigger would spin a
      phantom follow-up bead for a healthy run.
    """
    if not result.executed:
        return None
    if spec.usage_format == CLAUDE_STREAM_JSON:
        usage = _claude_last_turn_usage(result.stdout)
        if usage is None:
            return None
        values = [usage[key] for key in _CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int)]
        return sum(values) if values else None
    if spec.usage_format == CODEX_JSONL:
        for usage in reversed(_codex_turn_usages(result.stdout)):
            values = [usage[key] for key in _CODEX_TOKEN_KEYS if isinstance(usage.get(key), int)]
            if values:
                return sum(values)
        return None
    return None


_VERSION_CACHE: dict[str, str | None] = {}


def adapter_version(spec: RunnerSpec) -> str | None:
    """The dispatched CLI's own version string, cached per process.

    D9 wants a dispatch reproducible in its inputs, and the adapter's version is
    one of them: the same prompt to a different CLI build is a different input.
    Probed once per spec — a probe failure records unknown rather than raising,
    since telemetry must never fail a dispatch.
    """
    if spec.name in _VERSION_CACHE:
        return _VERSION_CACHE[spec.name]
    version: str | None = None
    executable = spec.command[0] if spec.command else None
    if executable and shutil.which(executable):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
            proc = subprocess.run(  # nosec B603
                [executable, "--version"],
                check=False,
                text=True,
                encoding="utf-8",
                capture_output=True,
                timeout=15,
            )
            if proc.returncode == 0:
                first = (proc.stdout or proc.stderr).strip().splitlines()
                version = first[0][:120] if first else None
    _VERSION_CACHE[spec.name] = version
    return version


def record_dispatch(  # noqa: PLR0913 — one parameter per recorded dispatch input
    repo_root: Path,
    issue_id: str,
    spec: RunnerSpec,
    result: RunResult,
    *,
    prompt: str | None = None,
    phase: str | None = None,
    scope_tokens: int | None = None,
    forecast_tokens: int | None = None,
    folded_info: tuple[str, ...] = (),
) -> None:
    """Persist a metadata-only run-record for one dispatch, keyed by the bead.

    Shared by every dispatch site — the loop's build dispatch, the supervisor's
    concurrent lanes, the rubric judge, and the decider — so one telemetry stream
    sees them all. Without this, a judged or decider dispatch spends real tokens
    that never reach ``run-records.json``, and the D3 grant ceiling under-counts
    the session (basicly-kjc5.31).

    The command is redacted (the prompt elided) before it reaches the record, so
    no prompt or secret is persisted. Usage is adapter-reported where the CLI
    emits it and a flagged chars/4 estimate otherwise.

    **Nothing here may raise.** This is telemetry on the critical path of every
    dispatch, so a defect in recording must never fail a landing. Deriving the
    command is the one step that can: ``format_command`` rejects a handoff-only
    spec, and a result can report execution while the resolved spec is a handoff
    runner — which is not hypothetical, it is what happens on a machine with no
    agent CLI installed, where ``select_runner`` resolves ``manual``
    (basicly-kjc5.53). A mismatch degrades to an empty command rather than an
    exception.
    """
    command: tuple[str, ...] = ()
    if not result.handoff:
        with contextlib.suppress(ValueError):
            command = tuple(format_command(spec, run_record.REDACTED_PROMPT, capture_usage=True))
    usage = extract_usage(spec, result)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None
    entry = run_record.build_record(
        agent=spec.name,
        handoff=result.handoff,
        returncode=result.returncode,
        duration_s=result.duration_s,
        command=command,
        model=spec.model,
        tokens=usage.tokens if usage else None,
        cost=usage.cost if usage else None,
        estimated=usage.estimated if usage else None,
        adapter_version=adapter_version(spec),
        prompt_sha256=digest,
        phase=phase,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        folded_info=folded_info,
    )
    with contextlib.suppress(OSError):
        run_record.record(repo_root, issue_id, entry)
    # The marker is the shared half: .basicly/usage/ never leaves this machine,
    # while br comments travel in issues.jsonl (D11). Best-effort — evidence
    # must never fail a landing.
    with contextlib.suppress(OSError, RuntimeError):
        run_record.record_marker(repo_root, issue_id, entry)


# --- Global agent-process budget (component 8, design section 6) --------------

# Reservation classes. Fixed semantics, deliberately not config (design section
# 6): `lane_slots` reserved for lane runners, exactly one reserved for the
# decider, and the remainder best-effort for read-only helpers.
LANE = "lane"
DECIDER = "decider"
HELPER = "helper"
PROCESS_CLASSES = (LANE, DECIDER, HELPER)

# The decider's reservation. One, and one is the point: a decision queue that
# cannot be worked because every process slot went to lanes is a deadlock, and
# the lanes are exactly what is waiting on those decisions.
DECIDER_SLOTS = 1

# Ceiling on concurrently live agent processes when nothing configures one
# (`[runner] max_agent_processes`). Owned here beside the other runner defaults;
# config re-exports it, because config imports this module and not the reverse.
DEFAULT_MAX_AGENT_PROCESSES = 8

# Seconds of no observed activity before a dispatch is flagged possibly-stuck
# (`[runner] stall_after`, design section 6). Distinct from `runner_timeout`
# (3600s): this one only raises a flag, it never kills.
DEFAULT_STALL_AFTER = 900.0


class BudgetExhaustedError(RuntimeError):
    """A helper slot was asked for from a budget that reserves none for helpers."""


class ProcessBudget:
    """Accounting for concurrently live agent processes, by reservation class.

    One global ceiling (``[runner] max_agent_processes``) split into three
    classes rather than multiplicative per-level caps. Lane runners and the
    decider draw on reservations, so a burst of read-only helpers can never
    starve them — and, critically, a helper never blocks a lane, so waiting for a
    helper slot cannot deadlock the pass.

    Helpers queue on the best-effort remainder. When the configured total leaves
    no remainder at all, a helper request is *refused* rather than queued:
    blocking on a queue that can never drain is a hang, and a clear refusal is
    the fail-closed behaviour (D9).
    """

    def __init__(self, total: int, lane_slots: int) -> None:
        """Split *total* slots into the three classes, reserving *lane_slots* for lanes."""
        # A ceiling below "the decider plus one lane" cannot run the factory at
        # all, so it is raised to that minimum rather than silently overcommitting
        # the machine (reservations summing past the ceiling) or refusing every
        # lane dispatch.
        self.total = max(DECIDER_SLOTS + 1, total)
        # The decider's slot is carved out *first*, before lanes. Its whole purpose
        # is to keep the decision queue workable, and the lanes are what wait on
        # those decisions — so a ceiling too small to hold both must narrow the
        # lane reservation, never drop the decider's. Reservations therefore never
        # exceed the ceiling, so the machine is never overcommitted.
        self.decider_slots = min(DECIDER_SLOTS, self.total)
        self.lane_slots = max(1, min(lane_slots, self.total - self.decider_slots))
        self.helper_slots = max(0, self.total - self.lane_slots - self.decider_slots)
        self._live: dict[str, int] = dict.fromkeys(PROCESS_CLASSES, 0)
        self._lock = threading.Lock()
        self._freed = threading.Condition(self._lock)

    def capacity(self, kind: str) -> int:
        """Slots reserved for *kind*."""
        if kind == LANE:
            return self.lane_slots
        if kind == DECIDER:
            return self.decider_slots
        if kind == HELPER:
            return self.helper_slots
        raise ValueError(f"unknown process class {kind!r}; expected one of {PROCESS_CLASSES}")

    def live(self, kind: str) -> int:
        """Processes of *kind* currently holding a slot."""
        self.capacity(kind)  # validates the class name
        with self._lock:
            return self._live[kind]

    @contextlib.contextmanager
    def slot(self, kind: str, *, timeout: float | None = None):
        """Hold one slot of *kind* for the duration of the block.

        Lane and decider acquisitions draw on their own reservations and so never
        wait on a helper. A helper waits for another helper to finish, up to
        *timeout* (None waits indefinitely — the queueing the design asks for).
        """
        capacity = self.capacity(kind)
        if capacity == 0:
            raise BudgetExhaustedError(
                f"max_agent_processes ({self.total}) reserves no slot for a {kind} process "
                f"(lane {self.lane_slots} + decider {self.decider_slots}); "
                "raise [runner] max_agent_processes to admit one"
            )
        deadline = None if timeout is None else time.monotonic() + timeout
        with self._freed:
            while self._live[kind] >= capacity:
                remaining = None if deadline is None else deadline - time.monotonic()
                if remaining is not None and remaining <= 0:
                    raise TimeoutError(
                        f"waited {timeout:.0f}s for a {kind} process slot "
                        f"({self._live[kind]}/{capacity} live)"
                    )
                self._freed.wait(remaining)
            self._live[kind] += 1
        try:
            yield
        finally:
            with self._freed:
                self._live[kind] -= 1
                self._freed.notify_all()


_BUDGET_LOCK = threading.Lock()
# A one-slot mapping rather than a rebound module global: same single instance,
# without a `global` statement in every accessor.
_BUDGET: dict[str, ProcessBudget] = {}


def configure_process_budget(total: int, lane_slots: int) -> ProcessBudget:
    """Install the process-wide budget; the first caller's numbers win.

    D1 puts one supervisor process in charge of the machine's concurrency, so it
    configures this at session start. First-caller-wins rather than
    last-caller-wins on purpose: re-deriving the ceiling while slots are held
    would let the live count exceed a shrunken capacity. Tests reset it.
    """
    with _BUDGET_LOCK:
        return _BUDGET.setdefault("current", ProcessBudget(total, lane_slots))


def process_budget() -> ProcessBudget:
    """The process-wide budget, built from the built-in defaults if unconfigured.

    A single-track session never configures one, so the defaults stand in — the
    accounting is still correct, just not the repo's numbers. The lane reservation
    follows the design's own rule of thumb (``max_agent_processes`` is about twice
    the worktree concurrency), so it cannot drift from the ceiling.
    """
    total = DEFAULT_MAX_AGENT_PROCESSES
    return configure_process_budget(total, max(1, total // 2))


def reset_process_budget() -> None:
    """Drop the configured budget (tests; never called in a real session)."""
    with _BUDGET_LOCK:
        _BUDGET.clear()


# --- Stall detection: flag a wedged dispatch without killing it ---------------


class StallWatchdog:
    """Flag a dispatch that shows no activity for *after* seconds (design section 6).

    A *flag*, not a kill. ``runner_timeout`` stays the only terminal action, so a
    slow-but-working run is never cut short — this exists so a human learns about
    a wedge in minutes instead of at the hard kill an hour later, while the wedged
    lane is still holding a concurrency slot.

    Activity is whatever *probe* returns: any change in that fingerprint counts as
    progress and restarts the clock. The supervisor fingerprints the lane's commits
    and worktree dirtiness, which is the real progress signal for a lane — it has
    to commit its work — and is far cheaper to sample than the agent's stdout,
    which the runner does not read incrementally.

    *on_stall* fires **once** per dispatch: the point is one queue item per wedged
    lane, not one per poll.
    """

    def __init__(
        self,
        after: float,
        probe: Callable[[], str],
        on_stall: Callable[[], object],
        *,
        poll: float | None = None,
    ) -> None:
        """Watch for *after* seconds of an unchanged *probe*, then call *on_stall* once."""
        self.after = after
        self._probe = probe
        self._on_stall = on_stall
        # Sample several times per window so the flag lands close to the deadline
        # rather than up to a whole window late.
        self._poll = poll if poll is not None else max(0.01, after / 4)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.flagged = False

    def _fingerprint(self) -> str:
        """The probe's reading, or a sentinel when probing itself fails.

        A probe that raises (a locked index, a vanished worktree) must never take
        down the dispatch it is only observing.
        """
        try:
            return self._probe()
        except OSError, RuntimeError, ValueError:
            return "<probe-failed>"

    def _watch(self) -> None:
        last = self._fingerprint()
        # Idle time is measured against a monotonic clock, not accumulated from
        # the nominal poll interval: `Event.wait(poll)` and the probe itself can
        # both overrun their budget on a loaded machine, and summing the nominal
        # figure would then need far more than `after` real seconds to fire — so
        # the flag on the very host most likely to wedge would arrive latest.
        quiet_since = time.monotonic()
        while not self._stop.wait(self._poll):
            current = self._fingerprint()
            if current != last:
                last, quiet_since = current, time.monotonic()
                continue
            if self.flagged or time.monotonic() - quiet_since < self.after:
                continue
            self.flagged = True
            # Contained: a failing notifier must not kill the watcher thread and
            # must never propagate into the dispatch.
            with contextlib.suppress(Exception):
                self._on_stall()

    def start(self) -> None:
        """Begin watching in a daemon thread."""
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        """Stop watching and join the thread."""
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def __enter__(self) -> StallWatchdog:
        """Start watching; the dispatch runs inside the block."""
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        """Stop watching, however the dispatch ended."""
        self.stop()

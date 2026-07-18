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

import shutil
import subprocess
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

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

# The name of the built-in handoff fallback runner.
MANUAL_RUNNER = "manual"

# Detection order for ``auto``: the big 3 by PATH, then the handoff fallback.
AUTO = "auto"
AUTO_ORDER = ("claude", "codex", "copilot")


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
    # Invocation-time tool-deny specs (basicly-lqz5). format_command emits one
    # `--deny-tool=<spec>` per entry after the binary. Populated for the copilot
    # runner from permissions.yaml at config load; empty leaves the argv unchanged.
    deny_tools: tuple[str, ...] = ()

    @property
    def binary(self) -> str | None:
        """The executable this runner shells out to, or None for a handoff."""
        return self.command[0] if self.command else None


# Built-in adapters. The big-3 command templates are best-effort defaults;
# they are config-overridable and every one is printable via `runner dry-run`.
BUILTIN_RUNNERS: tuple[RunnerSpec, ...] = (
    RunnerSpec("claude", HEADLESS, ("claude", "-p", PROMPT_PLACEHOLDER)),
    RunnerSpec("codex", HEADLESS, ("codex", "exec", PROMPT_PLACEHOLDER)),
    RunnerSpec("copilot", HEADLESS, ("copilot", "-p", PROMPT_PLACEHOLDER)),
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


def format_command(spec: RunnerSpec, prompt: str) -> list[str]:
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
    # contract); deny-tool flags then follow the model.
    return _apply_model(spec, _apply_deny_tools(spec, argv))


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
    """Inject one ``--deny-tool=<spec>`` per entry after the binary (basicly-lqz5).

    Empty ``deny_tools`` leaves the argv unchanged. The ``--deny-tool=`` (single
    token) form is used so a spec containing spaces — e.g. ``shell(git push
    --force)`` — stays one argv element and is never mis-parsed as the next flag.
    """
    if not spec.deny_tools:
        return argv
    flags = [f"--deny-tool={tool}" for tool in spec.deny_tools]
    return [argv[0], *flags, *argv[1:]]


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


def run(spec: RunnerSpec, prompt: str, cwd: Path, *, dry_run: bool = False) -> RunResult:
    """Invoke *spec* on *prompt* in *cwd*, capturing output.

    A handoff runner never executes — it returns a handoff result so the caller
    surfaces the prompt and leaves the work to the driving agent/human. A dry run
    returns the exact argv without executing it.
    """
    if spec.kind == HANDOFF:
        return RunResult(spec.name, (), executed=False, handoff=True)
    argv = format_command(spec, prompt)
    if dry_run:
        return RunResult(spec.name, tuple(argv), executed=False)
    stdin = prompt if spec.prompt_via == "stdin" else None
    start = time.perf_counter()
    proc = subprocess.run(  # nosec B603
        argv, cwd=cwd, input=stdin, capture_output=True, text=True, check=False
    )
    duration_s = time.perf_counter() - start
    return RunResult(
        spec.name,
        tuple(argv),
        executed=True,
        returncode=proc.returncode,
        stdout=proc.stdout,
        stderr=proc.stderr,
        duration_s=duration_s,
    )

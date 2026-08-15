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
import re
import shutil
import signal
import subprocess
import threading
import time
import uuid
from collections.abc import Callable, Mapping
from dataclasses import dataclass, replace
from pathlib import Path
from typing import IO

from . import context_window, models, run_record
from .context_window import ADAPTER_WINDOW, ADAPTER_WINDOWS, DEFAULT_CONTEXT_WINDOW, FALLBACK_WINDOW
from .copilot_store import COPILOT_SESSION_STORE, shutdown_data, store_usage
from .redact import redact_secrets
from .runner_envelope import (
    CLAUDE_JSON,
    CLAUDE_STREAM_JSON,
    CLAUDE_SUBAGENT_TYPE,
    CLAUDE_TOKEN_KEYS,
    CODEX_JSONL,
    CODEX_TOKEN_KEYS,
    UNNAMED_SUBAGENT,
    claude_last_turn_usage,
    claude_result_event,
    claude_result_field,
    codex_agent_message,
    codex_turn_usages,
    forwarded,
    stream_events,
    stream_object,
)
from .runner_usage import (
    Usage,
    claude_json_usage,
    claude_turn_usage,
    codex_jsonl_usage,
    codex_turn_usage,
    floor_usage,
)

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

# How a family spells "dispatch as this role" on its argv (RunnerSpec.agent_style).
# Both installed families take `--agent <name>` and resolve it against the agent
# root `basicly install` wrote — verified against claude 2.1.226 and copilot 1.0.78
# on 2026-08-09, not recalled. Codex is deliberately absent: it ships no subagent
# root, so a role cannot be selected there and the parity gap is declared at the
# spec rather than discovered when a flag is silently ignored.
AGENT_NAME_FLAG = "agent-name"
AGENT_STYLES = (AGENT_NAME_FLAG,)

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

# Every usage-report format a spec may declare. The enumeration lives here rather
# than with either reader because dispatching on the format is this module's job:
# three of them name an envelope in captured stdout (:mod:`basicly.runner_envelope`)
# and the fourth names a store the agent writes itself (:mod:`basicly.copilot_store`),
# and only a caller that has to pick between them needs the union. None means the CLI
# reports no usage at all, so the chars/4 transcript estimate applies.
USAGE_FORMATS = (CLAUDE_JSON, CLAUDE_STREAM_JSON, CODEX_JSONL, COPILOT_SESSION_STORE)

# Flags appended for a usage-capturing dispatch. Trailing — after the prompt —
# so a subcommand invocation like `codex exec` keeps the flag inside the
# subcommand; both CLIs accept options after positional arguments. Kept out of
# spec.command so the --help capability probe is untouched (same stance as
# sandbox/approval).
_USAGE_FLAGS = {
    CLAUDE_JSON: ("--output-format", "json"),
    # claude refuses stream-json under -p without --verbose. `--forward-subagent-text`
    # rides with them because this is the one dispatch shape it is legal on (2.1.226
    # `--help`: "only works with --print and --output-format=stream-json"). Without it
    # a lane that delegates goes silent for the whole nested run — no event reaches the
    # stream, so neither the quiet bound nor a watching human can tell it from a wedge.
    CLAUDE_STREAM_JSON: (
        "--output-format",
        "stream-json",
        "--verbose",
        "--forward-subagent-text",
    ),
    CODEX_JSONL: ("--json",),
    # The one format whose flag takes a value: the per-dispatch session UUID is
    # appended by _apply_usage, because it is what extract_usage later reads the
    # store by.
    COPILOT_SESSION_STORE: ("--session-id",),
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
    # Optional portable model tier (`schema.MODEL_TIERS`), resolved to a concrete
    # model at dispatch through the committed map (basicly-kjc5.59). The whole
    # point of a tier is that no provider model id lives in a projected agent
    # file, so this is the declaration and `model` above is the resolved result:
    # an explicit `model` therefore wins, and a tier that cannot resolve refuses
    # the dispatch rather than falling back to some other tier's model.
    tier: str | None = None
    # Which vendor's model a tier resolves to. Only meaningful on a multi-vendor
    # surface — copilot serves four vendors — so None takes the family default
    # from models.FAMILY_MODEL_SURFACES.
    vendor: str | None = None
    # Where `tier` above came from, for the run record's provenance. Set by the
    # config loader, which is also what applies `[runner] default_tier` to a spec
    # declaring none: defaulting on the *spec* rather than at each call site means
    # every dispatch path gets it without threading a parameter through seven of
    # them, and a dispatch site added later cannot forget to.
    tier_source: str | None = None
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
    # How this family selects a projected agent, or None when it cannot. Separate
    # from `deny_style` because the two are independent: codex denies tools and
    # cannot select a role.
    agent_style: str | None = None
    # Invocation-time sandbox/approval guardrails (basicly-t0kt). Codex forbids
    # overriding approval_policy/sandbox_mode at repo scope in .codex/config.toml
    # by design, so safe defaults cannot be projected as committed catalog output;
    # the only seam is the invocation. format_command emits `--sandbox <mode>` and
    # `-a <policy>` after the binary when set. Defaulted for the codex runner
    # (`workspace-write` disables network by default; `never` fails safe in
    # headless exec — no approver, so an escalation is denied, not auto-granted,
    # and execution failures are returned to the model instead). The intended
    # `on-failure` is not in the CLI's enum, so every codex dispatch died at
    # argument parsing until basicly-jr0l.38; of the three accepted values
    # (untrusted, on-request, never) only `never` avoids escalating to an
    # approver who cannot exist, with the sandbox as the real safety boundary.
    # :func:`check_guardrails` now validates these against the installed CLI.
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
    # USAGE_FORMATS or None. None — the CLI reports no token usage in any
    # envelope basicly can read — makes a usage-capturing dispatch fall back to
    # the chars/4 transcript estimate (design 7.5).
    usage_format: str | None = None
    # Base directory of the agent's own per-session usage store, for a
    # ``copilot-session-store`` dispatch (basicly-2rn9). None uses
    # DEFAULT_COPILOT_SESSION_STORE; ``[runner] copilot_session_store`` sets it.
    session_store: Path | None = None
    # The model's context window in tokens (basicly-kjc5.6): the threshold the
    # [policy.sizing] context_ceiling meter takes *before* a dispatch (design D8),
    # config-overridable per agent. What the finished run was measured against is
    # resolved per dispatch in :mod:`basicly.context_window`, because the window
    # belongs to the model and only the run itself can say which one it used.
    context_window: int = DEFAULT_CONTEXT_WINDOW
    # Which input decided the window above (basicly-23ep): one of ADAPTER_WINDOW,
    # FALLBACK_WINDOW, AGENT_WINDOW or DECLARED_WINDOW. Carried for the same reason
    # `tier_source` is — the number alone cannot say whether anyone chose it, and a
    # window nobody chose is the defect this field exists to make visible. None only
    # on a spec built by hand in a test.
    context_window_source: str | None = None

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
        agent_style=AGENT_NAME_FLAG,
        usage_format=CLAUDE_STREAM_JSON,
        context_window=ADAPTER_WINDOWS["claude"].tokens,
        context_window_source=ADAPTER_WINDOW,
    ),
    RunnerSpec(
        "codex",
        HEADLESS,
        ("codex", "exec", PROMPT_PLACEHOLDER),
        sandbox="workspace-write",
        approval="never",
        usage_format=CODEX_JSONL,
        # Defaulted, not checked: codex reports no window on any event of its `--json`
        # stream (probed 0.146.0, 2026-08-15), so nothing here could ever refute this
        # figure. It stays as the observation floor and never reaches a run record.
        context_window=400_000,
        context_window_source=FALLBACK_WINDOW,
    ),
    RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        deny_style=DENY_TOOL_FLAG,
        agent_style=AGENT_NAME_FLAG,
        usage_format=COPILOT_SESSION_STORE,
        # Nor does copilot: `session.shutdown` carries `modelMetrics` and no window key
        # on 6 of 6 local stores, so its figure is the same conservative default.
        context_window_source=FALLBACK_WINDOW,
    ),
    # The handoff runner dispatches nothing, so its window is inert — but labelling
    # it keeps "no source recorded" meaning what it says: a spec nobody built through
    # the config loader.
    RunnerSpec(MANUAL_RUNNER, HANDOFF, context_window_source=FALLBACK_WINDOW),
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
    # A terminal bound stopped the dispatch and its process tree was hard-killed
    # (basicly-kjc5.7, design section 6): the supervisor routes this to the
    # decision queue as a stall flag. returncode is None whenever it is set.
    timed_out: bool = False
    # *Which* bound ended it, when the wall clock was not the one that did
    # (basicly-lpsf). None on a clean run, and None on a `runner_timeout` kill —
    # so every existing reader of `timed_out` keeps its meaning and only the
    # message a routed outcome carries has to tell the bounds apart. Read it
    # through :func:`stop_label`.
    stopped: StopReason | None = None
    # The session id this dispatch supplied on its argv, when its usage format
    # measures out of band (basicly-2rn9). It is the key
    # :func:`extract_usage` reads the agent's own usage store by, so it has to
    # survive the dispatch; None whenever no store was keyed.
    session_id: str | None = None
    # How this dispatch's model was decided (basicly-kjc5.59). Carried on the
    # result rather than recomputed by the recorder, because resolution happens
    # once — inside run(), before anything is spawned — and its provenance (which
    # tier, from which input, honoured or not) is the part telemetry needs and
    # cannot re-derive afterwards. None when no model and no tier were in play.
    model_resolution: models.ModelResolution | None = None


def format_command(
    spec: RunnerSpec,
    prompt: str,
    *,
    capture_usage: bool = False,
    session_id: str | None = None,
    role: str | None = None,
) -> list[str]:
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
    object). A consumer that parses the agent's answer must read it back through
    :func:`result_text`, which undoes the envelope — that is what lets the
    metered dispatches keep their answers (basicly-gczc). What still leaves it
    unset is the two CLI passthroughs, ``basicly review`` and ``basicly runner
    run``: both print the agent's output straight to a human and write no
    run-record, so there is nothing to meter and nothing to re-parse.

    *session_id* is the store key for a usage format that measures out of band
    rather than on stdout (``copilot-session-store``, basicly-2rn9). Data, never
    generated here, so the argv stays a pure function of its inputs — :func:`run`
    mints one per dispatch and hands the same value back on the result.

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
    # contract); sandbox/approval and deny-tool flags then follow the model. The
    # role goes outermost of all, so `--agent <role>` reads first on a logged argv
    # — that line is how an operator tells a specialised dispatch from a default
    # one, and it is worth nothing if it is buried behind six tool denials.
    argv = _apply_deny_tools(spec, argv)
    argv = _apply_role(spec, _apply_model(spec, _apply_sandbox(spec, argv)), role)
    return _apply_usage(spec, argv, session_id) if capture_usage else argv


def _apply_usage(spec: RunnerSpec, argv: list[str], session_id: str | None) -> list[str]:
    """Append the usage-report flags for a usage-capturing dispatch (basicly-kjc5.1).

    No format leaves the argv unchanged — the dispatch still runs, and
    :func:`extract_usage` falls back to the transcript estimate. An unknown
    format raises: the config parser validates the value, so this is reachable
    only from a hand-built spec.

    ``copilot-session-store`` is the one format whose flag carries a value: the
    session id names the store the usage will be read from. Without one there is
    no store to key on, so the flag is omitted and that dispatch meters by
    estimate rather than by a store path nobody can find.
    """
    if spec.usage_format is None:
        return argv
    flags = _USAGE_FLAGS.get(spec.usage_format)
    if flags is None:
        raise ValueError(
            f"runner {spec.name!r} has unknown usage_format {spec.usage_format!r}; "
            f"known: {list(USAGE_FORMATS)}"
        )
    if spec.usage_format == COPILOT_SESSION_STORE:
        return [*argv, *flags, session_id] if session_id else argv
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


# Provenance labels for a resolved model, recorded verbatim on the run record.
AGENT_MODEL_PIN = "agent model pin"
AGENT_TIER = "agent tier"
FAMILY_DEFAULT_TIER = "family default tier"


def model_family(spec: RunnerSpec) -> str:
    """Which runner family *spec* belongs to, for the model-surface lookup.

    The binary decides it, not the agent name: a ``[[runner.agents]]`` entry may
    call itself anything while still shelling out to ``copilot``, and it is the
    binary that fixes which model spelling is accepted. Falls back to the name for
    a handoff runner, which has no binary at all.
    """
    binary = spec.binary
    if binary is None:
        return spec.name
    return Path(binary).stem.lower()


def resolve_model(
    spec: RunnerSpec,
    *,
    repo_root: Path | None = None,
    mapping: dict | None = None,
) -> models.ModelResolution:
    """Decide the model for one dispatch of *spec*, or refuse.

    Order, most specific first (basicly-kjc5.59): an explicit ``model`` pin, then
    the spec's ``tier``. An explicit pin wins because a tier exists to *avoid*
    naming a provider id — so someone who named one anyway has overridden the
    mechanism deliberately. ``[runner] default_tier`` is already folded onto the
    spec by the config loader, which is why there is no third branch here and no
    dispatch site has to remember to pass it.

    Raises :class:`models.ModelResolutionError` when a tier was asked for and no
    model can be pinned, naming the agent and the config key. Nothing has been
    spawned at that point, which is the whole value of resolving up front.

    A tier aimed at a family that cannot express a model at all — the handoff
    runner has no argv — comes back ``honoured=False`` with the reason, so the
    run record says the dispatch ran on the session's own model rather than
    implying the tier was satisfied.
    """
    if spec.model is not None:
        return models.ModelResolution(model=spec.model, source=AGENT_MODEL_PIN)
    tier = spec.tier
    if tier is None:
        return models.ModelResolution()
    source = spec.tier_source or AGENT_TIER
    family = model_family(spec)
    surfaces = models.FAMILY_MODEL_SURFACES.get(family)
    if spec.kind == HANDOFF or surfaces is None:
        return models.ModelResolution(
            tier=tier,
            source=source,
            honoured=False,
            note=(
                f"runner {spec.name!r} has no model flag to pin a tier onto, so the "
                f"dispatch ran on the session's own model and tier {tier!r} was not applied"
            ),
        )
    surface, default_vendor = surfaces
    vendor = spec.vendor or default_vendor
    try:
        model = models.model_for(tier, vendor, surface, mapping=mapping, repo_root=repo_root)
    except (models.ModelUnavailableError, models.ModelMapError) as exc:
        key = "default_tier" if source == FAMILY_DEFAULT_TIER else "tier"
        raise models.ModelResolutionError(
            f"runner {spec.name!r} declares model tier {tier!r} ({source}) but it "
            f"resolves to no model: {exc}. Set a reachable tier or an explicit model on "
            f"[[runner.agents]] {key} for {spec.name!r}, or point it at a vendor that "
            f"serves that tier on the {surface!r} surface"
        ) from exc
    return models.ModelResolution(model=model, tier=tier, source=source)


def _apply_role(spec: RunnerSpec, argv: list[str], role: str | None) -> list[str]:
    """Inject the family's agent-selection flag for *role* (basicly-4kdm).

    No role, or a family with no ``agent_style``, leaves the argv unchanged — which
    is the codex path and the default-runner path, and both must keep working
    exactly as they did. A role on a family that cannot select one is **dropped
    rather than raised**, unlike ``deny_tools``: a denial silently lost is a
    guarantee silently lost, while a role silently lost is only a dispatch that
    runs unspecialised. The asymmetry is deliberate and it is the difference
    between a safety flag and a routing flag.

    The caller is expected to have resolved the role against the projected agent
    root already (:func:`roles.resolve_role`), because this function cannot see the
    repository and a name that does not resolve would put a flag on the argv that
    the host drops without a word.
    """
    if role is None or spec.agent_style is None:
        return argv
    if spec.agent_style != AGENT_NAME_FLAG:
        raise ValueError(
            f"runner {spec.name!r} has agent_style {spec.agent_style!r}; "
            f"known: {list(AGENT_STYLES)}"
        )
    return [argv[0], "--agent", role, *argv[1:]]


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
        # `binary` is a configured runner name already resolved on PATH and the argv is
        # a literal flag; nothing here is caller-supplied.
        proc = subprocess.run(  # noqa: S603 — configured binary, literal argv
            [binary, HELP_FLAG], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.SubprocessError:
        return None
    return (proc.stdout or "") + (proc.stderr or "")


# The guardrail attributes format_command injects, and the CLI flag whose
# enumerated values govern each (basicly-jr0l.38). The spec carries the value;
# only the installed CLI knows the accepted set, so it is read from `--help`.
_GUARDRAIL_FLAGS: tuple[tuple[str, str], ...] = (
    ("sandbox", "--sandbox"),
    ("approval", "--ask-for-approval"),
)

# clap renders an option's enum two ways, and codex uses both: inline for
# `--sandbox`, an indented bullet list for `--ask-for-approval`.
_INLINE_VALUES = re.compile(r"\[possible values:\s*([^\]]*)\]", re.IGNORECASE)
_VALUES_HEADING = re.compile(r"^\s*possible values:\s*$", re.IGNORECASE | re.MULTILINE)
_VALUE_BULLET = re.compile(r"^\s*-\s+([A-Za-z0-9][\w-]*)\s*(?::|$)")
# The start of the *next* option entry, which bounds one option's help slice. A
# value bullet ("- never:") is not one: it is a single dash followed by a space.
_NEXT_OPTION = re.compile(r"^(\s*)(?:-[A-Za-z0-9], --[\w-]+|--[\w-]+)")


def possible_values(help_text: str, flag: str) -> tuple[str, ...] | None:
    """The values *flag* enumerates in *help_text*, or None when it enumerates none.

    None is the "cannot tell" answer and covers both a flag the help text never
    mentions and one documented without an enum — callers must not read it as
    "no value is accepted". Only a non-empty tuple is positive evidence.
    """
    lines = help_text.splitlines()
    start = next((i for i, line in enumerate(lines) if _mentions_flag(line, flag)), None)
    if start is None:
        return None
    indent = len(lines[start]) - len(lines[start].lstrip())
    end = len(lines)
    for i in range(start + 1, len(lines)):
        match = _NEXT_OPTION.match(lines[i])
        if match and len(match.group(1)) <= indent:
            end = i
            break
    return _values_in("\n".join(lines[start:end]))


def _mentions_flag(line: str, flag: str) -> bool:
    """True when *line* is the option entry that defines *flag* (not a mere mention)."""
    match = _NEXT_OPTION.match(line)
    return match is not None and flag in re.split(r"[,\s=<]+", line.strip())


def _values_in(slice_text: str) -> tuple[str, ...] | None:
    """Parse either clap enum rendering out of one option's help slice."""
    if inline := _INLINE_VALUES.search(slice_text):
        values = tuple(v.strip() for v in inline.group(1).split(",") if v.strip())
        return values or None
    if heading := _VALUES_HEADING.search(slice_text):
        bullets = tuple(
            m.group(1)
            for line in slice_text[heading.end() :].splitlines()
            if (m := _VALUE_BULLET.match(line))
        )
        return bullets or None
    return None


def check_guardrails(spec: RunnerSpec, *, help_text: str | None = None) -> tuple[str, ...]:
    """Guardrail values *spec* pins that the installed CLI's ``--help`` rejects.

    One message per offending attribute, naming the value and the accepted set,
    so a misconfiguration is reported at check time rather than as an opaque
    exit 2 at dispatch. Empty means nothing was disproved: an unset guardrail, a
    CLI whose help does not enumerate the flag, or an unreadable probe all pass,
    on the same positive-evidence-only rule :func:`probe_capability` follows —
    a check that guesses would false-skip a working agent.
    """
    if spec.kind != HEADLESS or spec.binary is None or help_text is None:
        return ()
    problems = []
    for attr, flag in _GUARDRAIL_FLAGS:
        value = getattr(spec, attr)
        accepted = possible_values(help_text, flag)
        if value is None or accepted is None or value in accepted:
            continue
        problems.append(
            f"{spec.binary} {flag} rejects {attr} {value!r}; accepts {', '.join(accepted)}"
        )
    return tuple(problems)


def probe_guardrails(
    spec: RunnerSpec, *, run: Callable[[str], str | None] | None = None
) -> tuple[str, ...]:
    """:func:`check_guardrails` against a live ``--help`` probe of *spec*'s binary."""
    if spec.kind != HEADLESS or spec.binary is None:
        return ()
    return check_guardrails(spec, help_text=(run or _run_help)(spec.binary))


def probe_capability(
    spec: RunnerSpec, *, run: Callable[[str], str | None] | None = None
) -> Capability:
    """Confirm *spec*'s assumed headless flag by probing its binary with ``--help``.

    ``flag_ok`` is False only on *positive* evidence — the probe ran and either a
    flag token is absent from the help output (the dropped/renamed-flag case) or
    a pinned sandbox/approval value is outside the enum the help advertises
    (basicly-jr0l.38, where the codex adapter pinned an approval the CLI rejected
    and every dispatch died at argument parsing). A handoff runner, a spec with
    no binary, or a probe that could not run assumes capable, so a flaky or slow
    probe never false-skips a working agent; PATH presence
    (:func:`is_available`) stays the primary signal.
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
    if rejected := check_guardrails(spec, help_text=out):
        return Capability(reachable=True, flag_ok=False, detail="; ".join(rejected))
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
        except subprocess.TimeoutExpired:
            continue
        else:
            return  # the group went down on the polite signal


def _taskkill_tree(pid: int) -> None:
    """Windows tree kill: ``taskkill /T`` walks the child chain from *pid*."""
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["taskkill", "/F", "/T", "/PID", str(pid)],  # noqa: S607 — a Windows system tool,
            # resolved from the system PATH by name because that is the only way it is reachable
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


# --- Incremental event stream: read the dispatch while it runs (basicly-rupz) --

# Usage formats whose stdout *is* an event stream — JSONL, one object per turn,
# emitted as the agent works rather than at its end. Only these have anything to
# observe mid-dispatch: ``claude-json`` emits its one object at the very end and
# ``copilot-session-store`` measures out of band, so both keep the single-buffer
# read and a sink handed to them is inert.
STREAMING_FORMATS = (CLAUDE_STREAM_JSON, CODEX_JSONL)

# How the stream reader decodes. Explicit, and the whole reason this is named
# rather than left default (basicly-6gkg): on the streaming path the decode
# happens on *our* reader threads, and a UnicodeDecodeError there kills the
# thread while the caller sees a clean exit and an empty transcript. The symptom
# is silence, in the one place it would be hardest to notice — a lane that
# printed a byte the platform encoding cannot represent would read as a lane
# that printed nothing. Replacement characters are the fail-safe: the stream
# keeps being read, and the undecodable run is visible as U+FFFD rather than as
# an absence.
STREAM_ERRORS = "replace"

# Ceiling on waiting for a reader thread once the process is down. Its pipes are
# closed by the exit, so a live reader finishes its last line in microseconds;
# one somehow still blocked is abandoned rather than waited on, exactly as
# :func:`_drain` abandons a descendant that still holds the pipe.
READER_JOIN_S = KILL_GRACE_S


@dataclass(frozen=True)
class StreamEvent:
    """One line of a streaming dispatch's stdout, observed while it still runs.

    *line* is the redacted text with its newline stripped, *data* the JSON object
    it parsed to (None for a plain-text line the CLI interleaved), and *usage*
    the per-turn token usage the event reported, when it carries one.

    *text* is the prose a human would read off the turn and *subagent* names the
    nested agent it came from, None when the lane agent produced it itself. They
    are what makes a dispatch watchable rather than merely metered: *line* and
    *usage* say a lane is alive and what it spent, never what it is doing.
    """

    line: str
    data: dict | None = None
    usage: Usage | None = None
    text: str | None = None
    subagent: str | None = None
    # Defaulted so every construction written before the field keeps working.
    tools: tuple[str, ...] = ()


# What a caller supplies to observe a dispatch as it happens. Return value
# ignored; see :func:`_emit` for why a raising sink is contained.
EventSink = Callable[[StreamEvent], object]


# --- Terminal bounds: what stops a dispatch before its own exit (basicly-lpsf) -

# The bound names a stopped dispatch is attributed to. Constants rather than
# literals because a routed outcome, a salvage commit message and a queue item
# all name the same one, and three spellings of "spend" would silently become
# three different bounds to a reader of the ledger.
SPEND_BOUND = "spend"
QUIET_BOUND = "quiet"

# Default seconds of a *silent* stream before a dispatch is stopped as wedged.
# Deliberately far above any single tool call a working lane makes: an agent
# emits nothing while one runs, so this bound must clear the longest legitimate
# one or it kills working lanes exactly as the wall clock did (basicly-yvx9).
# The longest such call this repo makes is its own test suite, measured at 76s
# (`uv run pytest -q`, 2552 passed, 2026-08-06), and this default sits 23x above
# it. It is deliberately *above* `DEFAULT_STALL_AFTER` (900s) too, so the
# human-facing flag always arrives first and a wedge can be intervened in before
# anything terminal happens to it.
#
# Uncalibrated, and it says so: no inter-event gap has ever been measured here,
# because until basicly-rupz the stream every metered lane emits was discarded
# unread. This is the first release that records one, so the figure to replace
# this with is the one the next passes produce.
DEFAULT_QUIET_AFTER = 1800.0

# Ceiling on how long a waiting dispatch goes between bound checks. Small enough
# that a bound lands promptly and large enough to be free next to the dispatch it
# watches — a `stop_when` that walks a ledger is the caller's cost to bound, which
# is why `supervise.SpendBound` snapshots rather than re-reads.
STOP_POLL_S = 0.5


@dataclass(frozen=True)
class StopReason:
    """Why a dispatch was stopped short of running to its own exit.

    *bound* is one of the module's ``*_BOUND`` names and *detail* is what a human
    reads on the queue item: the numbers the bound compared, never a restatement
    of its name.
    """

    bound: str
    detail: str


# Consulted while a streaming dispatch runs; a reason stops it, None lets it run.
StopCheck = Callable[[], "StopReason | None"]


@dataclass(frozen=True)
class DispatchBounds:
    """The terminal bounds a streaming dispatch is stopped on, ahead of the wall clock.

    The wall clock was the working bound and is now the backstop (basicly-lpsf).
    It had to be, because it was the only terminal signal there was — and it is
    the one signal that carries no information about whether work is happening,
    so it was calibrated inside the upper tail of real work: the longest
    *successful* lane measured 1712s against an 1800s cap, 95.1% of it. These are
    the bounds that do carry that information, and both are reachable only
    because the dispatch's event stream is now read as it arrives.

    * *quiet_after* — seconds of a silent stream before the dispatch is stopped
      as wedged. An event is proof of life whether or not any file changed, which
      is what the git-state probe behind :class:`StallWatchdog` could never say.
    * *stop_when* — an arbitrary predicate, re-read every :meth:`interval` while
      the dispatch runs. What the supervisor puts here is the D3 spend ceiling:
      tokens accrue monotonically and are the resource that actually matters, so
      a spend bound is strictly better than any clock. Its input only ever moves
      when a usage event lands, so the bound is reached within one interval of
      the event that reached it — the predicate is polled, the *quantity* it
      reads is event-driven.

    Both are optional and a bounds object with neither set is inert, which leaves
    the dispatch on the wall clock alone exactly as before.
    """

    quiet_after: float | None = None
    stop_when: StopCheck | None = None

    @property
    def armed(self) -> bool:
        """Whether anything here can stop a dispatch."""
        return self.quiet_after is not None or self.stop_when is not None

    def interval(self) -> float:
        """How long to wait between bound checks.

        Several samples per quiet window, for the reason :class:`StallWatchdog`
        samples that way: a bound checked once per window lands up to a whole
        window late. :data:`STOP_POLL_S` is the ceiling, so a bound with no quiet
        window still lands within half a second of the event that reached it.
        """
        if self.quiet_after is None:
            return STOP_POLL_S
        return max(0.01, min(STOP_POLL_S, self.quiet_after / 4))


def stop_label(result: RunResult, timeout: float) -> str:
    """How a killed dispatch's terminal bound reads on a queue item or salvage commit.

    One spelling for every surface that reports a kill — the routed outcome, the
    stall item, the salvage commit trailer — so a bound cannot be described one
    way in the tracker and another in git history.
    """
    if result.stopped is not None:
        return f"{result.stopped.bound} bound: {result.stopped.detail}"
    return f"runner_timeout after {timeout:.0f}s"


def _streaming(spec: RunnerSpec, *, capture_usage: bool) -> bool:
    """Whether a dispatch of *spec* emits an event stream a sink can be driven from."""
    return capture_usage and spec.usage_format in STREAMING_FORMATS


def _emit(spec: RunnerSpec, line: str, on_event: EventSink) -> None:
    """Redact one stdout line, parse it, and hand the event to *on_event*.

    Redacted *before* parsing, so nothing a sink can reach — neither the raw text
    nor the decoded object — carries a credential the agent echoed
    (basicly-3p2i). Two facts make that ordering safe on JSON: the
    ``<redacted:...>`` placeholder is legal inside a JSON string, and the generic
    secret-assignment rule cannot match a JSON member at all, because a JSON key
    is followed by a closing quote where the rule needs its ``:``. A redaction
    that did break a line would degrade it to a text-only event — which is what an
    unparseable line already becomes — and never to a wrong total, because every
    total is parsed from the raw buffer :func:`_read_streaming` keeps.

    Contained: a sink that raises must not take down the reader thread and leave
    the rest of the dispatch unobserved, the same stance :class:`StallWatchdog`
    takes on its notifier.
    """
    text = redact_secrets(line.rstrip("\n"))
    data = stream_object(text)
    # The four readers are total over any JSON object — each guards every field
    # with isinstance and returns None rather than raising on a shape it does not
    # recognise. That is not tidiness: they run on the stdout reader thread, so a
    # raise here would end the read with the dispatch still running, leaving the
    # rest of its output unobserved and its totals taken from a cut transcript.
    event = (
        StreamEvent(line=text)
        if data is None
        else StreamEvent(
            line=text,
            data=data,
            usage=event_usage(spec, data),
            text=event_text(spec, data),
            subagent=event_subagent(spec, data),
            tools=event_tools(spec, data),
        )
    )
    with contextlib.suppress(Exception):
        on_event(event)


def _pump(stream: IO[str], on_line: Callable[[str], object]) -> None:
    """Hand every line of *stream* to *on_line* until EOF, then close it.

    One thread per pipe, which is what ``communicate`` was doing for us and what
    keeps either pipe from filling while the other is read. A read that fails
    because the process went down mid-line is the end of the stream, not an error:
    the caller already has the exit status.
    """
    try:
        for line in iter(stream.readline, ""):
            on_line(line)
    except OSError, ValueError:
        return
    finally:
        with contextlib.suppress(OSError, ValueError):
            stream.close()


@dataclass(frozen=True)
class _Streamed:
    """What :func:`_read_streaming` collected, shaped like ``communicate`` plus status."""

    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    stopped: StopReason | None = None


class _Liveness:
    """When this dispatch last emitted, for the quiet bound to measure against.

    Stamped on the runner's stdout reader thread and read from the thread waiting
    on the process, so both ends take the lock. Monotonic, for the reason
    :class:`StallWatchdog` measures idle time that way: a wall-clock reading can
    step backwards and would hand the wedged lane a fresh window.
    """

    def __init__(self) -> None:
        """Start the quiet window at the moment the dispatch was handed over."""
        self._lock = threading.Lock()
        self._at = time.monotonic()

    def stamp(self) -> None:
        """Record an event having just arrived."""
        with self._lock:
            self._at = time.monotonic()

    def quiet_for(self) -> float:
        """Seconds since the last event, or since the dispatch started."""
        with self._lock:
            return time.monotonic() - self._at


def _bound_reached(bounds: DispatchBounds, liveness: _Liveness) -> StopReason | None:
    """Which of *bounds* this dispatch has reached, or None while it is inside them.

    Quiet first, because a wedged dispatch reports no usage and would otherwise be
    attributed to whichever bound happened to be checked first. A *stop_when* that
    raises is treated as "no reason": the predicate reads a tracker and a run-record
    file, and a transient failure there must not become a kill — the wall-clock
    backstop is still underneath, which is the whole point of keeping it.
    """
    if bounds.quiet_after is not None and liveness.quiet_for() >= bounds.quiet_after:
        return StopReason(QUIET_BOUND, f"no stream events for {bounds.quiet_after:g}s")
    if bounds.stop_when is None:
        return None
    try:
        return bounds.stop_when()
    except OSError, RuntimeError, ValueError:
        return None


def _read_streaming(  # noqa: PLR0913 — one parameter per independent read input
    proc: subprocess.Popen[str],
    spec: RunnerSpec,
    stdin: str | None,
    timeout: float | None,
    on_event: EventSink,
    bounds: DispatchBounds | None = None,
) -> _Streamed:
    """Read *proc* to completion line by line, handing stdout events to *on_event*.

    Replaces ``communicate`` on a streaming dispatch and keeps both of its
    guarantees. Neither pipe may fill, so stderr gets a reader of its own that
    does nothing but collect. And the returned buffers must be exactly what
    ``communicate`` would have returned — so the lines are kept **raw** and
    joined, with redaction applied to the sink's copy and, by the caller, to the
    whole transcript. That is what makes this additive: ``extract_usage`` and
    every total downstream of it read the same bytes they read before.

    Owns its own timeout rather than raising into :func:`run`'s handler: the
    reader threads hold the pipes, so ``_drain``'s ``communicate`` would find them
    closed and report nothing — and a killed dispatch's partial transcript is
    exactly what the salvage commit and the chars/4 floor read.

    *bounds* are the terminal bounds ahead of that wall clock (basicly-lpsf).
    Reaching one kills the tree the same way a timeout does and returns the same
    partial transcript — the run is stopped either way, and the only difference is
    which bound the outcome is attributed to.
    """
    out_lines: list[str] = []
    err_lines: list[str] = []
    liveness = _Liveness()

    def observe(line: str) -> None:
        out_lines.append(line)
        liveness.stamp()
        _emit(spec, line, on_event)

    readers = (
        threading.Thread(target=_pump, args=(proc.stdout, observe), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err_lines.append), daemon=True),
    )
    for reader in readers:
        reader.start()
    if stdin is not None and proc.stdin is not None:
        # Written from this thread only because the readers above are already
        # draining — that is the deadlock ``communicate`` exists to avoid. A
        # child that exited before reading it is not an error here; its status is.
        with contextlib.suppress(OSError, ValueError):
            proc.stdin.write(stdin)
            proc.stdin.close()
    outcome = _wait_bounded(proc, timeout, bounds, liveness)
    for reader in readers:
        reader.join(READER_JOIN_S)
    return replace(outcome, stdout="".join(out_lines), stderr="".join(err_lines))


def _wait_bounded(
    proc: subprocess.Popen[str],
    timeout: float | None,
    bounds: DispatchBounds | None,
    liveness: _Liveness,
) -> _Streamed:
    """Wait for *proc*, killing it on whichever bound it reaches first.

    Unbounded, this is one ``proc.wait(timeout)`` and behaves exactly as it did.
    Bounded, the same wait is taken in :meth:`DispatchBounds.interval` slices so
    the bounds can be re-checked between them — the process is still *waited* on,
    never polled for liveness, so a dispatch that exits inside a slice is
    collected immediately rather than at the end of it.

    The remaining wall clock is recomputed against a monotonic start on every
    pass instead of being decremented by the nominal slice, for the reason
    :class:`StallWatchdog` measures its window that way: a slice and a bound check
    can both overrun on a loaded machine, and summing the nominal figure would
    push the backstop out by however loaded the box is.
    """
    started = time.monotonic()
    while True:
        remaining = None if timeout is None else timeout - (time.monotonic() - started)
        if remaining is not None and remaining <= 0:
            _kill_tree(proc)
            return _Streamed("", "", None, timed_out=True)
        if bounds is None or not bounds.armed:
            slice_s = remaining
        elif remaining is None:
            slice_s = bounds.interval()
        else:
            slice_s = min(bounds.interval(), remaining)
        try:
            return _Streamed("", "", proc.wait(timeout=slice_s), timed_out=False)
        except subprocess.TimeoutExpired:
            pass
        if bounds is None or not bounds.armed:
            # The slice *was* the whole remaining wall clock, so the next pass
            # takes the timeout branch above; no bound can be reached here.
            continue
        reason = _bound_reached(bounds, liveness)
        if reason is not None:
            _kill_tree(proc)
            # `timed_out` as well as `stopped`: the dispatch was hard-killed with
            # its tree, which is the fact every existing routing path keys on, and
            # `stopped` is what tells the surfaces that report it which bound did
            # it. A stopped run that claimed a clean exit would land as a green
            # lane carrying half a change.
            return _Streamed("", "", None, timed_out=True, stopped=reason)


def run(  # noqa: PLR0913 — mirrors the CLI surface
    spec: RunnerSpec,
    prompt: str,
    cwd: Path,
    *,
    dry_run: bool = False,
    capture_usage: bool = False,
    timeout: float | None = None,
    on_event: EventSink | None = None,
    bounds: DispatchBounds | None = None,
    role: str | None = None,
) -> RunResult:
    """Invoke *spec* on *prompt* in *cwd*, capturing output.

    A handoff runner never executes — it returns a handoff result so the caller
    surfaces the prompt and leaves the work to the driving agent/human. A dry run
    returns the exact argv without executing it. *capture_usage* asks the CLI to
    report token usage (see :func:`format_command`); parse the result with
    :func:`extract_usage`. *timeout* hard-kills the dispatch after that many
    seconds (basicly-kjc5.7): the result comes back ``timed_out`` with whatever
    output was captured, so the caller can route the stall instead of hanging.
    It is the **backstop**, not the working bound (basicly-lpsf) — see *bounds*.
    The kill takes the dispatch's **whole process tree** with it — see
    :func:`_kill_tree`; an agent CLI's children must not outlive the stall that
    was queued for it (basicly-kjc5.15). Rescuing the buffered output is only half
    of what a kill strands: the *worktree* holds the run's actual value, and
    committing it is the caller's job (:func:`commit.salvage`, basicly-yvx9) —
    this layer owns the process, not the tree.

    A declared model tier is resolved here, before anything is spawned
    (basicly-kjc5.59): an unresolvable tier raises
    :class:`models.ModelResolutionError` and no agent process starts, so the
    dispatch never silently runs on the wrong model. The tier is read off the
    spec, where the config loader has already applied ``[runner] default_tier``.

    *on_event* observes the dispatch **while it runs** (basicly-rupz). A metered
    dispatch on a :data:`STREAMING_FORMATS` adapter already asks its CLI for a
    per-turn event stream; supplying a sink is what stops that stream being paid
    for and thrown away. The pipes are then read line by line on their own reader
    threads, each stdout line reaching the sink as a :class:`StreamEvent` — proof
    of life for a watchdog, and per-turn usage for a spend meter that would
    otherwise learn what the lane cost only once the run record was written.

    Additive by construction: the buffers this returns are the same ones the
    single-read path returns, so :func:`extract_usage` and every total downstream
    of it are unmoved. A sink handed to a non-streaming adapter is inert — an
    out-of-band format has no stream to assume — and no sink at all keeps the
    dispatch on ``communicate`` exactly as before.

    *bounds* are the terminal bounds that stop the dispatch ahead of *timeout*
    (:class:`DispatchBounds`, basicly-lpsf) — a silent stream, and whatever the
    caller's own predicate decides, which for a lane is the D3 spend ceiling.
    They ride the same reader the sink does, so they need one: bounds supplied
    without a sink, or to an adapter with no stream, leave the dispatch on the
    wall clock alone. Reaching one is reported as ``timed_out`` with
    :attr:`RunResult.stopped` naming the bound, because a bound stop *is* a hard
    kill — same tree kill, same partial transcript, same salvage — and only the
    attribution differs.
    """
    # Resolve first, and ahead of the handoff return, so a refusal costs no
    # process and a handoff still records that its tier could not be honoured.
    resolution = resolve_model(spec, repo_root=cwd)
    carried = resolution if (resolution.model or resolution.tier) else None
    if resolution.model is not None:
        spec = replace(spec, model=resolution.model)
    if spec.kind == HANDOFF:
        return RunResult(spec.name, (), executed=False, handoff=True, model_resolution=carried)
    # A store-measured format needs its store key minted *before* the dispatch:
    # supplying the new session's UUID is what makes the store path knowable
    # without scraping stdout, so the plain-text output stays plain text
    # (basicly-2rn9). Formats that report on stdout need no key.
    session_id = (
        str(uuid.uuid4()) if capture_usage and spec.usage_format == COPILOT_SESSION_STORE else None
    )
    argv = format_command(
        spec, prompt, capture_usage=capture_usage, session_id=session_id, role=role
    )
    if dry_run:
        return RunResult(
            spec.name,
            tuple(argv),
            executed=False,
            session_id=session_id,
            model_resolution=carried,
        )
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
    stopped: StopReason | None = None
    # The sink survives only for a dispatch that has a stream to feed it; for any
    # other adapter it is dropped here, which is what makes it inert rather than a
    # promise the format cannot keep.
    sink = on_event if _streaming(spec, capture_usage=capture_usage) else None
    # Popen, not subprocess.run: run's timeout kills only the direct child, and
    # the dispatch must be started in its own process group to be killable as a
    # tree at all (basicly-kjc5.15). POSIX gets a new session, whose id is the
    # child's pid — that is what lets os.killpg reach every descendant. Windows
    # has no equivalent for signalling a tree (taskkill /T walks it instead); it
    # gets its own group only so a stray Ctrl-C cannot cross over. Each flag is
    # inert on the other platform.
    proc = subprocess.Popen(  # noqa: S603 — argv is the engine-built spec, no shell
        argv,
        cwd=cwd,
        stdin=stdin_source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        # Only on the streaming path, and only there because that is the path
        # whose decode happens on a reader thread we own, where the error is
        # silent (basicly-6gkg, :data:`STREAM_ERRORS`). ``communicate`` below
        # keeps the default so its behaviour is untouched.
        errors=STREAM_ERRORS if sink is not None else None,
        env=env,
        start_new_session=os.name != "nt",
        creationflags=CREATE_NEW_PROCESS_GROUP if os.name == "nt" else 0,
    )
    try:
        if sink is not None:
            streamed = _read_streaming(proc, spec, stdin, timeout, sink, bounds)
            stdout, stderr = streamed.stdout, streamed.stderr
            returncode: int | None = streamed.returncode
            timed_out = streamed.timed_out
            stopped = streamed.stopped
        else:
            stdout, stderr = proc.communicate(input=stdin, timeout=timeout)
            returncode = proc.returncode
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
        if sink is None:
            # The reader threads already own the pipes on the streaming path, so
            # ``_drain``'s ``communicate`` would find them closed and report
            # nothing; the kill above is the whole of what an interrupt needs.
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
        stopped=stopped,
        session_id=session_id,
        model_resolution=carried,
    )


def extract_usage(spec: RunnerSpec, result: RunResult) -> Usage | None:
    """Token usage for *result*: adapter-reported when parseable, else estimated.

    None when nothing executed (a handoff or a dry run) — there is no transcript
    to meter. A spec whose format is None (the CLI reports no usage) or output
    that does not parse falls back to a chars/4 estimate over the captured
    transcript, flagged ``estimated`` so calibration can down-weight it
    (design 7.5).

    ``copilot-session-store`` is measured out of band: the numbers come from the
    agent's own session store rather than from the captured output, keyed by the
    session id the dispatch supplied (basicly-2rn9). An absent or unreadable
    store takes the very same estimate fallback, flagged the same way — a
    measurement that could not be made is never reported as one that was.

    A dispatch that died before its agent process started has no adapter to ask,
    but the engine's own captured error *is* the whole transcript — so the floor
    over it is a real bound rather than the structural under-count it is for an
    agent run (basicly-jr0l.64). It is still flagged ``estimated``; what makes it
    safe to meter is the record's ``unstarted`` outcome, not the number.
    """
    if result.handoff:
        return None
    if not result.executed:
        captured = result.stdout or result.stderr
        return floor_usage(result.stdout, result.stderr) if captured else None
    reported: Usage | None = None
    if spec.usage_format == CLAUDE_JSON:
        reported = claude_json_usage(result.stdout)
    elif spec.usage_format == CLAUDE_STREAM_JSON:
        reported = claude_json_usage(claude_result_event(result.stdout))
    elif spec.usage_format == CODEX_JSONL:
        reported = codex_jsonl_usage(result.stdout)
    elif spec.usage_format == COPILOT_SESSION_STORE:
        reported = store_usage(spec, result.session_id)
    if reported is not None:
        return reported
    return floor_usage(result.stdout, result.stderr)


def result_text(spec: RunnerSpec, stdout: str) -> str:
    """The agent's **own answer**, unwrapped from whatever usage envelope carries it.

    The inverse of :func:`_apply_usage` for the output side (basicly-gczc). A
    usage-capturing dispatch on a stdout-reporting adapter no longer prints the
    agent's reply as plain text: claude wraps it in a result object, codex in a
    JSONL event stream. So a caller that both meters a dispatch *and* parses its
    reply has to read the reply from the envelope rather than from stdout — which
    is what lets the decider (:func:`basicly.decisions.invoke_decider`) and the
    rubric judge (:func:`basicly.rubrics.evaluate`) be metered at all, instead of
    trading their answer for their token count.

    Each field was read off a live probe of the shape the engine actually
    dispatches, not from documentation: ``claude -p --output-format json``'s
    single object and ``--output-format stream-json --verbose``'s terminating
    ``result`` event both carry the reply on ``result``, and
    ``codex … exec --json``'s reply is the ``text`` of the **last**
    ``item.completed`` event whose item is an ``agent_message`` (codex-cli
    0.146.0). ``copilot-session-store`` measures out of band, so its stdout was
    never wrapped and comes back untouched (basicly-2rn9) — the same property
    that made it the cheap arm.

    Falls back to *stdout* verbatim when no envelope parses: an adapter that did
    not produce the shape its format claims has no reply hidden anywhere else, and
    the transcript is the only text there is. That degrades to the pre-metering
    behaviour rather than blanking a reply — and both callers fail closed on it
    anyway (an abstention, an ``UNKNOWN`` verdict), because an envelope is not a
    parseable answer to either of them.
    """
    if spec.usage_format == CLAUDE_JSON:
        unwrapped = claude_result_field(stdout)
    elif spec.usage_format == CLAUDE_STREAM_JSON:
        unwrapped = claude_result_field(claude_result_event(stdout))
    elif spec.usage_format == CODEX_JSONL:
        unwrapped = codex_agent_message(stdout)
    else:
        return stdout
    return unwrapped if unwrapped is not None else stdout


def event_usage(spec: RunnerSpec, event: dict) -> Usage | None:
    """The per-turn usage *event* reports, or None when it carries none.

    The incremental half of :func:`extract_usage` (basicly-rupz): what one turn
    cost, read off the event as it arrives rather than off the terminal result
    object at the end.

    **Summing these does not converge on the recorded total** (basicly-jr0l.67).
    Measured over four lanes, the live sum runs 1.46x to at least 1.79x the tokens
    :func:`extract_usage` writes to the run record, roughly constant rather than
    growing with turn count. Any caller comparing a live sum against a
    record-denominated figure is mixing denominations and must scale — see
    :data:`supervise.LIVE_OVERREPORT_BOUND`, which exists because comparing them
    directly killed a lane with a third of its grant unspent.

    The paragraph below is the claim that measurement refuted, kept because the
    reasoning is what a fix has to disprove or repair:

    Summing these across a dispatch converges on the very total
    :func:`extract_usage` reports — claude's result event re-counts
    ``cache_read_input_tokens`` per turn exactly as the per-turn blocks do, and
    codex's total *is* the sum over ``turn.completed`` — so a live meter built on
    them is denominated in the same quantity as the grant it spends against, which
    is the whole point of metering mid-dispatch at all.

    Converges on, never replaces. The terminal object stays the one thing that
    reaches the run record, so nothing here can move a recorded total.
    """
    if spec.usage_format == CLAUDE_STREAM_JSON:
        return claude_turn_usage(event)
    if spec.usage_format == CODEX_JSONL:
        return codex_turn_usage(event)
    return None


def event_text(spec: RunnerSpec, event: dict) -> str | None:
    """The prose *event* carries, for a sink to show as a progress line.

    Claude only: codex reports its reply as one terminal ``agent_message`` that
    :func:`codex_agent_message` already reads. Thinking blocks are excluded —
    each arrives with a signature blob many times the length of its prose. None
    when the turn carried no text, the common case: a tool-calling turn's content
    is a ``tool_use`` block with nothing to show beyond having happened.
    """
    if spec.usage_format != CLAUDE_STREAM_JSON:
        return None
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return None
    parts = [
        block["text"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "text"
        and isinstance(block.get("text"), str)
    ]
    return "\n".join(parts).strip() or None


def event_tools(spec: RunnerSpec, event: dict) -> tuple[str, ...]:
    """The tools *event*'s turn called, in the order it emitted them.

    What separates a turn that read from a turn that wrote, which is the split
    ``basicly-ejdm`` needs and had no instrument for. Claude only, like its
    siblings: codex emits no per-tool event this stack parses.

    A caller pricing these must pair them: a ``tool_use`` block is the cost of
    *emitting* the call, while the tool's result lands in the next turn's
    ``cache_creation_input_tokens``.
    """
    if spec.usage_format != CLAUDE_STREAM_JSON:
        return ()
    message = event.get("message")
    content = message.get("content") if isinstance(message, dict) else None
    if not isinstance(content, list):
        return ()
    return tuple(
        block["name"]
        for block in content
        if isinstance(block, dict)
        and block.get("type") == "tool_use"
        and isinstance(block.get("name"), str)
    )


def event_subagent(spec: RunnerSpec, event: dict) -> str | None:
    """Which nested subagent *event* was forwarded from, or None for a lane turn.

    The ``subagent_type`` claude puts on a forwarded event, falling back to
    :data:`UNNAMED_SUBAGENT` when it names none — so a sink always tells nested
    work apart without reading :data:`CLAUDE_PARENT_TOOL_USE_ID` itself.
    """
    if spec.usage_format != CLAUDE_STREAM_JSON or not forwarded(event):
        return None
    kind = event.get(CLAUDE_SUBAGENT_TYPE)
    return kind if isinstance(kind, str) and kind else UNNAMED_SUBAGENT


# --- Observed model: what the adapter says it actually ran (basicly-kjc5.59) ---
#
# Probed against the installed CLIs 2026-07-31 rather than assumed, because the
# three families differ in kind and one of them cannot answer at all:
#
# - claude 2.1.220 reports it three ways on `-p --output-format stream-json`: the
#   `system` init event's `model`, every `assistant` event's `message.model`, and
#   the terminating `result` event's `modelUsage` map. `modelUsage` is read first
#   because it is keyed per model and so survives a mid-run switch, and each
#   block's `canonicalModel` is preferred over the key: the key is the **dated**
#   build (`claude-haiku-4-5-20251001`) while `canonicalModel`
#   (`claude-haiku-4-5`) is exactly the map's anthropic-surface spelling.
# - copilot 1.0.77 reports it as the `session.shutdown` `modelMetrics` **keys**
#   (28 of 28 local stores that carried metrics). A single dispatch can list more
#   than one, so this returns a tuple rather than a single id.
# - codex 0.146.0 reports it **nowhere**: its whole `--json` stream is
#   thread.started / turn.started / item.completed / turn.completed, with no model
#   field on any of them. So codex yields `()` — unobserved, never fabricated.


def observed_models(spec: RunnerSpec, result: RunResult) -> tuple[str, ...]:
    """The models the adapter reports this dispatch actually used, in stream order.

    Empty when nothing executed, when the family reports no model (codex), or when
    the envelope did not parse. Empty means *unobserved*, which is deliberately
    distinct from "ran the model we pinned" — a mismatch cannot be claimed either
    way without evidence.
    """
    if not result.executed:
        return ()
    if spec.usage_format in (CLAUDE_JSON, CLAUDE_STREAM_JSON):
        streaming = spec.usage_format == CLAUDE_STREAM_JSON
        return _claude_observed_models(result.stdout, streaming=streaming)
    if spec.usage_format == COPILOT_SESSION_STORE:
        return _copilot_observed_models(spec, result.session_id)
    return ()


def _dedup(names: list[str]) -> tuple[str, ...]:
    """The names in first-seen order, without repeats."""
    seen: dict[str, None] = {}
    for name in names:
        if isinstance(name, str) and name:
            seen.setdefault(name, None)
    return tuple(seen)


def _claude_observed_models(stdout: str, *, streaming: bool) -> tuple[str, ...]:
    """Models named by a claude envelope: `modelUsage` first, then the turns."""
    payload = claude_result_event(stdout) if streaming else stdout
    found: list[str] = []
    try:
        obj = json.loads(payload.strip() or "null")
    except json.JSONDecodeError:
        obj = None
    if isinstance(obj, dict):
        usage_by_model = obj.get("modelUsage")
        if isinstance(usage_by_model, dict):
            for key, block in usage_by_model.items():
                canonical = block.get("canonicalModel") if isinstance(block, dict) else None
                found.append(canonical if isinstance(canonical, str) and canonical else key)
    if not found and streaming:
        for event in stream_events(stdout):
            message = event.get("message")
            # A forwarded subagent turn names the subagent's model, which is its
            # own tier's and not the lane's pin — counting it here would report a
            # `model_mismatch` for a dispatch that honoured its pin exactly.
            if forwarded(event):
                continue
            if event.get("type") == "assistant" and isinstance(message, dict):
                model = message.get("model")
                if isinstance(model, str):
                    found.append(model)
            elif event.get("type") == "system" and isinstance(event.get("model"), str):
                found.append(event["model"])
    return _dedup(found)


def _copilot_observed_models(spec: RunnerSpec, session_id: str | None) -> tuple[str, ...]:
    """Models named by a copilot session store: the `modelMetrics` keys."""
    data = shutdown_data(spec, session_id)
    metrics = data.get("modelMetrics") if data is not None else None
    if not isinstance(metrics, dict):
        return ()
    return _dedup(list(metrics))


def model_mismatch(pinned: str | None, observed: tuple[str, ...]) -> str | None:
    """A description of the pin the adapter did not honour, or None.

    None when nothing was pinned, when nothing was observed, or when the pin
    matches an observed model under :func:`models.same_model` — which tolerates
    the surface spellings and dated builds that make literal equality useless
    here. A real divergence returns prose rather than a bool so the run record
    names both sides, because "the model differed" is unactionable without them.
    """
    if pinned is None or not observed:
        return None
    if any(models.same_model(pinned, seen) for seen in observed):
        return None
    return f"pinned {pinned!r} but the adapter reported {', '.join(repr(o) for o in observed)}"


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
    - ``copilot-session-store``: **None**, deliberately, for now. The shutdown
      event does carry a real occupancy view (``currentTokens`` alongside
      ``systemTokens``/``conversationTokens``/``toolDefinitionsTokens``), so
      wiring the copilot ceiling is a genuine follow-on rather than a
      limitation — it is left out of basicly-2rn9 because that bead is scoped to
      the cost meter, and turning a ceiling on is a behaviour change that wants
      its own bead.
    - No usage format, nothing executed, or a parse miss: None. The chars/4
      transcript estimate is deliberately *not* used here — stdout length says
      nothing about window occupancy, and a false ceiling trigger would spin a
      phantom follow-up bead for a healthy run.
    """
    if not result.executed:
        return None
    if spec.usage_format == CLAUDE_STREAM_JSON:
        usage = claude_last_turn_usage(result.stdout)
        if usage is None:
            return None
        values = [usage[key] for key in CLAUDE_TOKEN_KEYS if isinstance(usage.get(key), int)]
        return sum(values) if values else None
    if spec.usage_format == CODEX_JSONL:
        for usage in reversed(codex_turn_usages(result.stdout)):
            values = [usage[key] for key in CODEX_TOKEN_KEYS if isinstance(usage.get(key), int)]
            if values:
                return sum(values)
        return None
    return None


def window_violations(history: Mapping[str, list], specs: Mapping[str, RunnerSpec]) -> list[str]:
    """Every recorded occupancy that exceeds its agent's declared window (basicly-23ep).

    The declaration's falsifier. :func:`context_occupancy` measures how full the
    model's window was at the *end* of a run, so it is denominated in the same
    quantity ``RunnerSpec.context_window`` bounds — and a lane cannot occupy more of
    a window than the window has. A record above the declaration is therefore not a
    lane that ran too big; it is proof the declaration describes a model the runtime
    no longer dispatches, which is exactly how ``claude`` sat at 200_000 while six
    lanes crossed the derived trigger on their way to a healthy finish.

    *history* is the dispatch ledger keyed by bead id (``run_record.dispatch_history``
    on the live tree), taken as a parameter rather than read here so the check can be
    exercised against a known-bad ledger instead of only against whatever this
    machine happens to have recorded. Each violation names **both** figures, because
    the number that has to change is not the one the reader is looking at.

    A record whose agent has no spec is skipped rather than flagged: an agent this
    config does not define has no declared window to contradict. That is the one
    silence here, and it is a property of the config, not of the ledger.
    """
    violations: list[str] = []
    for bead_id, entries in sorted(history.items()):
        for entry in entries:
            if not isinstance(entry, Mapping):
                continue
            occupancy = entry.get("context_tokens")
            if isinstance(occupancy, bool) or not isinstance(occupancy, int):
                continue
            agent = entry.get("agent")
            spec = specs.get(agent) if isinstance(agent, str) else None
            if spec is None or occupancy <= spec.context_window:
                continue
            violations.append(
                f"{bead_id} recorded a context occupancy of {occupancy:,} tokens on runner "
                f"{spec.name!r}, above its declared context_window of "
                f"{spec.context_window:,} ({spec.context_window_source or 'source unrecorded'}); "
                f"a run cannot occupy more of a window than the window has, so the "
                f"declaration is wrong — raise it to the window the model this runner "
                f"dispatches actually has"
            )
    return violations


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
            # `executable` passed `shutil.which` on the line above, and the argv is a literal.
            proc = subprocess.run(  # noqa: S603 — which()-resolved, literal argv
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
    stopped_bound: str | None = None,
    scope_tokens: int | None = None,
    forecast_tokens: int | None = None,
    forecast_spend_tokens: int | None = None,
    task_class: str | None = None,
    forecast_source: str | None = None,
    build_factor_source: str | None = None,
    folded_info: tuple[str, ...] = (),
    dispatch_rank: int | None = None,
    scheduler_rank: int | None = None,
    scheduler_fallback_rank: int | None = None,
    scheduler_score: int | None = None,
    scheduler_policy: str | None = None,
) -> None:
    """Persist a metadata-only run-record for one dispatch, keyed by the bead.

    Shared by every dispatch site — the loop's build dispatch, the supervisor's
    concurrent lanes, the rubric judge, and the decider — so one telemetry stream
    sees them all. Without this, a judged or decider dispatch spends real tokens
    that never reach ``run-records.json``, and the D3 grant ceiling under-counts
    the session (basicly-kjc5.31).

    The command is redacted (the prompt elided) before it reaches the record, so
    no prompt or secret is persisted. Usage is adapter-reported wherever the CLI
    reports it — on stdout, or in its own session store — and a flagged chars/4
    estimate otherwise. The run's final context occupancy lands beside its
    forecast as ``context_tokens``, which is the only measurement of the quantity
    the sizing band gates on (basicly-fcls).

    **Nothing here may raise.** This is telemetry on the critical path of every
    dispatch, so a defect in recording must never fail a landing.

    The command is copied off the result, never re-derived, which was wrong both ways:
    0 of 357 records named ``--agent`` though the lane passes one (basicly-jn1x), and
    the derivation hard-coded ``capture_usage`` true while the decider dispatches it
    unset (basicly-tcmy.33). It also retires the one step that could raise, a
    handoff-only spec reaching ``format_command`` (basicly-kjc5.53). Redaction matches
    the dispatched prompt, so it survives the flags layered over it; an unknown prompt
    records no argv rather than publishing one into a committed ledger.
    """
    command: tuple[str, ...] = ()
    if prompt is not None:
        command = tuple(
            run_record.REDACTED_PROMPT if arg == prompt else arg for arg in result.command
        )
    usage = extract_usage(spec, result)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None
    # Model provenance comes off the result, where run() left it, rather than being
    # re-resolved: re-resolving would read the map a second time and could answer
    # differently from the dispatch that actually happened (basicly-kjc5.59).
    resolution = result.model_resolution
    pinned = resolution.model if resolution is not None else spec.model
    seen = observed_models(spec, result)
    window, window_source = context_window.resolve(
        declared=spec.context_window,
        source=spec.context_window_source,
        reported=context_window.reported_window(result.stdout)
        if result.executed and spec.usage_format == CLAUDE_STREAM_JSON
        else None,
    )
    entry = run_record.build_record(
        agent=spec.name,
        handoff=result.handoff,
        # A dispatch that never spawned a process is labelled as such rather than
        # as a failed agent run, which is what keeps its estimate from halting the
        # grant like an unmeterable *run* does (basicly-jr0l.64, policy.session_spend).
        started=result.executed,
        returncode=result.returncode,
        duration_s=result.duration_s,
        command=command,
        model=pinned,
        model_tier=resolution.tier if resolution is not None else None,
        model_source=resolution.source if resolution is not None else None,
        tier_honoured=resolution.honoured if resolution is not None else None,
        observed_models=seen,
        model_mismatch=model_mismatch(pinned, seen),
        tokens=usage.tokens if usage else None,
        cost=usage.cost if usage else None,
        estimated=usage.estimated if usage else None,
        input_tokens=usage.input_tokens if usage else None,
        output_tokens=usage.output_tokens if usage else None,
        cache_read_tokens=usage.cache_read_tokens if usage else None,
        cache_write_tokens=usage.cache_write_tokens if usage else None,
        reasoning_tokens=usage.reasoning_tokens if usage else None,
        credits=usage.credits if usage else None,
        adapter_version=adapter_version(spec),
        prompt_sha256=digest,
        phase=phase,
        # Passed in rather than read off `result` here, so the ledger records the
        # bound the *dispatch site* acted on. The two can only agree, and a record
        # that derived it independently would be a second opinion about a fact the
        # routing has already used (basicly-lpsf).
        stopped_bound=stopped_bound,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        # The forecast in the unit this record's `tokens` is metered in — whole-lane
        # spend, not working set (basicly-tcmy.34). Passed in rather than computed
        # here: it is resolved from the estimate that gated the dispatch, and
        # re-deriving it at record time would answer with a calibration the gate
        # never saw.
        forecast_spend_tokens=forecast_spend_tokens,
        # The actual beside the forecast (basicly-fcls). Computed here rather than
        # passed in for the same reason the usage split is: every dispatch site
        # already hands over its forecast, and a second site deciding whether to
        # measure the outcome is how the pair stops being a pair.
        context_tokens=context_occupancy(spec, result),
        # The denominator that occupancy was measured against, and where it came
        # from (basicly-23ep). Recorded rather than re-derived at read time for the
        # reason every sibling provenance field is: the config moves, and a record
        # carrying an occupancy whose window has since changed cannot say which
        # declaration its ceiling actually fired under. None is a real answer here —
        # nothing declared a window and the adapter reported none (basicly-89hm).
        context_window=window,
        context_window_source=window_source,
        task_class=task_class,
        forecast_source=forecast_source,
        build_factor_source=build_factor_source,
        folded_info=folded_info,
        dispatch_rank=dispatch_rank,
        scheduler_rank=scheduler_rank,
        scheduler_fallback_rank=scheduler_fallback_rank,
        scheduler_score=scheduler_score,
        scheduler_policy=scheduler_policy,
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

    A *flag*, not a kill — and deliberately the earliest of the three bounds, so a
    human learns about a wedge while it is still theirs to intervene in rather than
    after something terminal has already happened to it. The terminal ones are
    :class:`DispatchBounds` and, underneath them, ``runner_timeout``
    (basicly-lpsf); ``stall_after`` sits below ``quiet_after`` for exactly that
    reason. A slow-but-working run is never cut short here.

    Activity is whatever *probe* returns: any change in that fingerprint counts as
    progress and restarts the clock. The supervisor fingerprints the lane's event
    stream *and* its commits and worktree dirtiness, so the lane is quiet only when
    both are — either alone has a blind spot, and an event is proof of life whether
    or not any file changed (basicly-rupz, :class:`basicly.supervise.LaneStream`).

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

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

from . import models, run_record
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
# copilot reports nothing usable on stdout — its result event carries
# premium-request counts, not tokens — but it writes a per-session event store,
# and that store's terminating `session.shutdown` event carries the per-model
# token split and the AI-credit spend (probed 1.0.75, present in 15 of 15 local
# sessions). So this format measures out of band: `--session-id <uuid>` *sets*
# the new session's id, which makes the store path known before the store
# exists, and stdout stays plain text — which is what keeps the rubric judge's
# text parser working on a metered dispatch (basicly-2rn9).
COPILOT_SESSION_STORE = "copilot-session-store"
USAGE_FORMATS = (CLAUDE_JSON, CLAUDE_STREAM_JSON, CODEX_JSONL, COPILOT_SESSION_STORE)

# Where copilot keeps its per-session event stores, and the stream inside one.
# Held unexpanded so no machine-specific path is committed and `Path.home()` is
# never called at import: the reader expands it at the point of use, which also
# lets a test (or `[runner] copilot_session_store`) redirect it to a temp dir.
DEFAULT_COPILOT_SESSION_STORE = Path("~/.copilot/session-state")
COPILOT_EVENTS_FILE = "events.jsonl"
COPILOT_SHUTDOWN_EVENT = "session.shutdown"

# The codex `--json` events that carry the agent's own reply, as opposed to its
# usage: an `item.completed` whose item is an `agent_message` (probed 0.146.0).
# :func:`result_text` reads the reply out of these so a metered codex dispatch
# still has a parseable answer.
CODEX_ITEM_COMPLETED = "item.completed"
CODEX_AGENT_MESSAGE = "agent_message"

# Context-window defaults per adapter (factory design §6, basicly-kjc5.6): the
# denominator for the context-ceiling meter. Conservative published windows;
# config-overridable per agent via `context_window` or `[runner] context_windows`.
# Unknown agents get the smallest of the big 3 so the ceiling errs toward
# finalizing early, never late.
#
# **These are defaults, not measurements, and a default that goes unchecked rots
# into a false capability claim** (basicly-23ep). Each was the published window of
# the model its CLI dispatched when it was written, and nothing re-reads the
# vendor's documentation afterwards — so a repo that upgrades its model keeps
# metering against the window of a model it no longer runs. On this tree that cost
# six spurious finalizations: `claude` declared 200_000 while lanes recorded a
# measured occupancy up to 223_221, which put the 0.6 trigger at one fifth of its
# intended point and spun a follow-up bead off every healthy long lane.
#
# Two consequences follow, and both are load-bearing:
#
# * The fix is NOT to write a bigger number here. A newer figure is the same
#   unchecked declaration one generation later, and it would hand a consumer
#   pinning a different model a window we invented for them. The window belongs in
#   the consuming repo's config, beside the model that repo actually dispatches —
#   which is why every resolved window now carries its own source
#   (:data:`ADAPTER_WINDOW` and friends), exactly as `tier_source` and
#   `forecast_source` do for their declarations.
# * A declaration this shape has to be falsifiable. `context_occupancy` measures
#   the same quantity the window bounds, so a recorded occupancy above the declared
#   window is a proof the declaration is wrong — :func:`window_violations` is that
#   proof, and `tests/test_runner.py` runs it over this repo's own ledger.
DEFAULT_CONTEXT_WINDOW = 128_000
_CONTEXT_WINDOWS = {"claude": 200_000, "codex": 400_000, "copilot": 128_000}

# Provenance labels for a resolved context window, recorded verbatim on the run
# record beside the window itself. The distinction that matters is declared versus
# defaulted: a defaulted window is a number nobody in this repo has checked against
# the model it dispatches, and reading one back as if it had been chosen is how
# basicly-23ep happened.
ADAPTER_WINDOW = "adapter default"  # _CONTEXT_WINDOWS, this adapter's published window
FALLBACK_WINDOW = "conservative fallback"  # DEFAULT_CONTEXT_WINDOW, an agent we know nothing about
AGENT_WINDOW = "agent context_window"  # [[runner.agents]] context_window
DECLARED_WINDOW = "[runner] context_windows"  # the per-agent declaration, most specific

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
    # The model's context window in tokens (basicly-kjc5.6): the denominator for
    # the [policy.sizing] context_ceiling meter (design D8). Per-adapter defaults
    # in _CONTEXT_WINDOWS; config-overridable per agent.
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
        usage_format=CLAUDE_STREAM_JSON,
        context_window=_CONTEXT_WINDOWS["claude"],
        context_window_source=ADAPTER_WINDOW,
    ),
    RunnerSpec(
        "codex",
        HEADLESS,
        ("codex", "exec", PROMPT_PLACEHOLDER),
        sandbox="workspace-write",
        approval="never",
        usage_format=CODEX_JSONL,
        context_window=_CONTEXT_WINDOWS["codex"],
        context_window_source=ADAPTER_WINDOW,
    ),
    RunnerSpec(
        "copilot",
        HEADLESS,
        ("copilot", "-p", PROMPT_PLACEHOLDER),
        deny_style=DENY_TOOL_FLAG,
        usage_format=COPILOT_SESSION_STORE,
        context_window=_CONTEXT_WINDOWS["copilot"],
        context_window_source=ADAPTER_WINDOW,
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
    # The dispatch hit [runner] runner_timeout and was hard-killed
    # (basicly-kjc5.7, design section 6): the supervisor routes this to the
    # decision queue as a stall flag. returncode is None on a timeout.
    timed_out: bool = False
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
    spec: RunnerSpec, prompt: str, *, capture_usage: bool = False, session_id: str | None = None
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
    # contract); sandbox/approval and deny-tool flags then follow the model.
    argv = _apply_model(spec, _apply_sandbox(spec, _apply_deny_tools(spec, argv)))
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

    A declared model tier is resolved here, before anything is spawned
    (basicly-kjc5.59): an unresolvable tier raises
    :class:`models.ModelResolutionError` and no agent process starts, so the
    dispatch never silently runs on the wrong model. The tier is read off the
    spec, where the config loader has already applied ``[runner] default_tier``.
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
    argv = format_command(spec, prompt, capture_usage=capture_usage, session_id=session_id)
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
        session_id=session_id,
        model_resolution=carried,
    )


@dataclass(frozen=True)
class Usage:
    """Token usage for one executed run: adapter-reported, or a chars/4 estimate."""

    # The single summed total processed. Every consumer of the run-record's
    # `tokens` reads it as that (the D3 grant ceiling, sizing calibration, the
    # cost rollups), so the split fields below are siblings, never a redefinition.
    tokens: int
    cost: float | None
    estimated: bool
    # Provider-neutral per-kind split, null for an adapter that reports no split
    # (basicly-2rn9). Each family's own summation semantics are folded in by its
    # extractor, so a reader never has to know whose numbers these were.
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    # AI credits, **not** USD. `cost` is USD (claude's total_cost_usd); copilot
    # meters in AIU. They are different units, so they get different fields —
    # adding them into one number would be a silent accounting defect.
    credits: float | None = None


# Claude usage-block keys: input_tokens excludes the cache fields (Anthropic
# usage semantics), so the total processed is the sum of all four.
_CLAUDE_TOKEN_KEYS = (
    "input_tokens",
    "cache_creation_input_tokens",
    "cache_read_input_tokens",
    "output_tokens",
)
# Codex usage keys summed into the total. Verified against codex-cli 0.146.0's
# own arithmetic by a live probe (2026-07-31, basicly-jr0l.37): a turn reporting
# input_tokens 12764, cached_input_tokens 9984, cache_write_input_tokens 0,
# output_tokens 155 and reasoning_output_tokens 147 is accounted
# total_tokens 12919 in the session rollout, and 12764 + 155 == 12919 exactly.
# So cached_input_tokens is a subset of input_tokens and reasoning_output_tokens
# is a subset of output_tokens (the probe's visible answer was 4 characters, so
# 155 - 147 is the answer plus framing) — adding either would double-count.
_CODEX_TOKEN_KEYS = ("input_tokens", "output_tokens")
# codex `turn.completed` usage keys mapped onto Usage's split fields
# (basicly-jr0l.37). `input_tokens` is the **superset**, exactly as copilot's
# `inputTokens` is: it already contains the cached portion, so the uncached
# remainder is `input_tokens - cache_read_tokens` rather than a fourth stored
# number. Same convention for both providers, so a cost model can read the split
# without knowing whose numbers these were.
_CODEX_USAGE_KEYS = {
    "input_tokens": "input_tokens",
    "output_tokens": "output_tokens",
    "cache_read_tokens": "cached_input_tokens",
    "cache_write_tokens": "cache_write_input_tokens",
    "reasoning_tokens": "reasoning_output_tokens",
}
# copilot `session.shutdown` per-model usage keys, mapped onto Usage's split
# fields. Summation semantics, verified against 15 local 1.0.75 stores:
# `inputTokens` *includes* both cache fields (inputTokens ==
# tokenDetails.input + cacheReadTokens + cacheWriteTokens held on all 15), so
# the total processed is inputTokens + outputTokens and adding the cache fields
# would double-count — the same subset relationship codex's cached_input_tokens
# has. `reasoningTokens` never exceeded `outputTokens`, so it is read as a
# subset of output too and likewise not added.
_COPILOT_USAGE_KEYS = {
    "input_tokens": "inputTokens",
    "output_tokens": "outputTokens",
    "cache_read_tokens": "cacheReadTokens",
    "cache_write_tokens": "cacheWriteTokens",
    "reasoning_tokens": "reasoningTokens",
}
# copilot meters AI credits in nano-AIU: a `totalNanoAiu` of 6_056_400_000 is
# 6.0564 credits (observed on the probe the test fixture was captured from).
_NANO_AIU_PER_CREDIT = 1_000_000_000


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
        return _floor_usage(result) if captured else None
    reported: Usage | None = None
    if spec.usage_format == CLAUDE_JSON:
        reported = _claude_json_usage(result.stdout)
    elif spec.usage_format == CLAUDE_STREAM_JSON:
        reported = _claude_json_usage(_claude_result_event(result.stdout))
    elif spec.usage_format == CODEX_JSONL:
        reported = _codex_jsonl_usage(result.stdout)
    elif spec.usage_format == COPILOT_SESSION_STORE:
        reported = _copilot_store_usage(spec, result.session_id)
    if reported is not None:
        return reported
    return _floor_usage(result)


def _floor_usage(result: RunResult) -> Usage:
    """The chars/4 floor over whatever transcript was captured (design 7.5)."""
    return Usage(tokens=(len(result.stdout) + len(result.stderr)) // 4, cost=None, estimated=True)


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
        unwrapped = _claude_result_field(stdout)
    elif spec.usage_format == CLAUDE_STREAM_JSON:
        unwrapped = _claude_result_field(_claude_result_event(stdout))
    elif spec.usage_format == CODEX_JSONL:
        unwrapped = _codex_agent_message(stdout)
    else:
        return stdout
    return unwrapped if unwrapped is not None else stdout


def _claude_result_object(stdout: str) -> dict | None:
    """Claude's result object, located rather than assumed to be all of *stdout*.

    Both readers below used to require ``stdout`` to be pure JSON, which made the
    non-streaming envelope intolerant of anything the CLI prints around it. The
    streaming reader never was — :func:`_claude_stream_events` skips lines it does
    not recognise — and the noise is not a property of the output format: the
    warning this module's own fixture pins ("no stdin data received in 3s") comes
    from the CLI's stdin handling, so a format that emits one object is exposed to
    it just the same. That was not observed on the ``json`` arm; it is inferred
    from the arm where it *was* observed, and hardened for because of what it
    costs. A leading line there reproduced both halves of basicly-gczc at once —
    the reply unreadable *and* the record estimated, which halts the grant — so
    the tolerant read is the one that cannot fail open.

    Takes the **last** parseable top-level object, matching the streaming
    reader's "last result event" rule, and falls back to parsing the whole
    transcript so a pretty-printed object spanning several lines still reads.
    """
    events = _claude_stream_events(stdout)
    if events:
        return events[-1]
    try:
        obj = json.loads(stdout.strip() or "null")
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def _claude_json_usage(stdout: str) -> Usage | None:
    """Parse claude's ``--output-format json`` result object (one JSON object).

    Tokens sum the usage block's input/output/cache fields; cost comes from
    ``total_cost_usd``. None on any parse miss so the caller falls back to the
    estimate.
    """
    obj = _claude_result_object(stdout)
    if obj is None or not isinstance(obj.get("usage"), dict):
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


def _claude_result_field(stdout: str) -> str | None:
    """The ``result`` string of claude's result object (one JSON object), or None.

    Shared by both claude envelopes because the streaming one ends in the very
    same object — see :func:`_claude_result_event`. None on any parse miss, or on
    a result field that is not a string (an ``is_error`` envelope can carry a
    structured payload there), so :func:`result_text` falls back to the
    transcript. An empty string is a real answer — the agent printed nothing —
    and is returned as one.
    """
    obj = _claude_result_object(stdout)
    if obj is None:
        return None
    value = obj.get("result")
    return value if isinstance(value, str) else None


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
    tokens sum across turns onto ``tokens``, and the per-kind counts sum onto the
    split fields (basicly-jr0l.37) so a cost model can price the cached portion
    an order of magnitude cheaper than the uncached one. ``tokens`` stays
    input + output — see :data:`_CODEX_TOKEN_KEYS` for the measured reason the
    cache and reasoning counts are subsets, not addends. Codex reports no cost.
    None when no usage event parses, so the caller falls back to the estimate.
    """
    total = 0
    found = False
    usages = _codex_turn_usages(stdout)
    for usage in usages:
        values = [usage[key] for key in _CODEX_TOKEN_KEYS if isinstance(usage.get(key), int)]
        if values:
            total += sum(values)
            found = True
    if not found:
        return None
    split = _codex_usage_split(usages)
    return Usage(
        tokens=total,
        cost=None,
        estimated=False,
        input_tokens=split["input_tokens"],
        output_tokens=split["output_tokens"],
        cache_read_tokens=split["cache_read_tokens"],
        cache_write_tokens=split["cache_write_tokens"],
        reasoning_tokens=split["reasoning_tokens"],
    )


def _codex_usage_split(usages: list[dict]) -> dict[str, int | None]:
    """Sum codex's per-kind token counts across turns, leaving an absent kind null.

    A count no turn reported stays None rather than 0, because those are
    different claims: 0.146.0 reports a real ``reasoning_output_tokens`` of 0 for
    a turn that did no reasoning, so a fabricated 0 for a build that omits the
    field would be indistinguishable from that measurement.
    """
    split: dict[str, int | None] = dict.fromkeys(_CODEX_USAGE_KEYS)
    for usage in usages:
        for field, key in _CODEX_USAGE_KEYS.items():
            value = usage.get(key)
            if isinstance(value, int) and not isinstance(value, bool):
                split[field] = (split[field] or 0) + value
    return split


def _codex_agent_message(stdout: str) -> str | None:
    """The text of the **last** ``agent_message`` item in codex's ``--json`` stream.

    The last one, not the concatenation: a multi-turn run emits one per turn, and
    the reply to the prompt is the final one — earlier ones are progress narration
    from before the tool calls. Scanned from the end for that reason, and
    unparseable lines are skipped like everywhere else in this module (a truncated
    final line is normal for a killed dispatch).

    None when no such item parses, so :func:`result_text` falls back to the
    transcript.
    """
    for line in reversed(stdout.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict) or event.get("type") != CODEX_ITEM_COMPLETED:
            continue
        item = event.get("item")
        if not isinstance(item, dict) or item.get("type") != CODEX_AGENT_MESSAGE:
            continue
        text = item.get("text")
        if isinstance(text, str):
            return text
    return None


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


def _copilot_shutdown_data(spec: RunnerSpec, session_id: str | None) -> dict | None:
    """The ``session.shutdown`` payload of one copilot session's store, or None.

    The store lives at ``<session_store>/<session_id>/events.jsonl`` — the
    directory name *is* the session id (checked on 15 of 15 local stores against
    each one's ``session.start``), which is what makes a supplied id a sound
    join. Scanned from the end because the shutdown event terminates the stream,
    and unparseable lines are skipped rather than failing the read: a truncated
    final line is normal for a killed dispatch.

    None for no session id, a store that is absent or unreadable, or a stream
    with no usable shutdown event. Never raises — the caller must be able to
    degrade to the estimate, and telemetry may not fail a dispatch.
    """
    if not session_id:
        return None
    base = spec.session_store or DEFAULT_COPILOT_SESSION_STORE
    events = base.expanduser() / session_id / COPILOT_EVENTS_FILE
    try:
        text = events.read_text(encoding="utf-8", errors="replace")
    except OSError:
        return None
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if not stripped.startswith("{"):
            continue
        try:
            event = json.loads(stripped)
        except json.JSONDecodeError:
            continue
        if isinstance(event, dict) and event.get("type") == COPILOT_SHUTDOWN_EVENT:
            data = event.get("data")
            if isinstance(data, dict):
                return data
    return None


def _copilot_store_usage(spec: RunnerSpec, session_id: str | None) -> Usage | None:
    """Measured usage for one copilot dispatch, read from its own session store.

    Sums the shutdown event's ``modelMetrics`` blocks, so a dispatch that
    switched model mid-run still meters once: per-kind tokens onto the split
    fields, ``totalNanoAiu`` onto ``credits``. ``tokens`` is input + output only
    (see :data:`_COPILOT_USAGE_KEYS` for why the cache and reasoning counts are
    subsets, not addends), and ``cost`` stays null because copilot bills in AI
    credits and that field is USD.

    None when no model block yields a token count, so the caller falls back to
    the flagged estimate.
    """
    data = _copilot_shutdown_data(spec, session_id)
    metrics = data.get("modelMetrics") if data is not None else None
    if not isinstance(metrics, dict):
        return None
    split = dict.fromkeys(_COPILOT_USAGE_KEYS, 0)
    nano_aiu: float | None = None
    measured = False
    for entry in metrics.values():
        if not isinstance(entry, dict):
            continue
        usage = entry.get("usage")
        if isinstance(usage, dict):
            for field, key in _COPILOT_USAGE_KEYS.items():
                value = usage.get(key)
                if isinstance(value, int) and not isinstance(value, bool):
                    split[field] += value
                    measured = True
        aiu = entry.get("totalNanoAiu")
        if isinstance(aiu, int | float) and not isinstance(aiu, bool):
            nano_aiu = (nano_aiu or 0.0) + float(aiu)
    if not measured:
        return None
    return Usage(
        tokens=split["input_tokens"] + split["output_tokens"],
        cost=None,
        estimated=False,
        input_tokens=split["input_tokens"],
        output_tokens=split["output_tokens"],
        cache_read_tokens=split["cache_read_tokens"],
        cache_write_tokens=split["cache_write_tokens"],
        reasoning_tokens=split["reasoning_tokens"],
        credits=None if nano_aiu is None else nano_aiu / _NANO_AIU_PER_CREDIT,
    )


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
    payload = _claude_result_event(stdout) if streaming else stdout
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
        for event in _claude_stream_events(stdout):
            message = event.get("message")
            if event.get("type") == "assistant" and isinstance(message, dict):
                model = message.get("model")
                if isinstance(model, str):
                    found.append(model)
            elif event.get("type") == "system" and isinstance(event.get("model"), str):
                found.append(event["model"])
    return _dedup(found)


def _copilot_observed_models(spec: RunnerSpec, session_id: str | None) -> tuple[str, ...]:
    """Models named by a copilot session store: the `modelMetrics` keys."""
    data = _copilot_shutdown_data(spec, session_id)
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
    # Model provenance comes off the result, where run() left it, rather than being
    # re-resolved: re-resolving would read the map a second time and could answer
    # differently from the dispatch that actually happened (basicly-kjc5.59).
    resolution = result.model_resolution
    pinned = resolution.model if resolution is not None else spec.model
    seen = observed_models(spec, result)
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
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        # The actual beside the forecast (basicly-fcls). Computed here rather than
        # passed in for the same reason the usage split is: every dispatch site
        # already hands over its forecast, and a second site deciding whether to
        # measure the outcome is how the pair stops being a pair.
        context_tokens=context_occupancy(spec, result),
        # The denominator that occupancy was measured against, and where it came
        # from (basicly-23ep). Recorded rather than re-derived at read time for the
        # reason every sibling provenance field is: the config moves, and a record
        # carrying an occupancy whose window has since changed cannot say which
        # declaration its ceiling actually fired under.
        context_window=spec.context_window,
        context_window_source=spec.context_window_source,
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

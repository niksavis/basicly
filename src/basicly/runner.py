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
from .checkout import sanitised_colour_env, sanitised_git_env
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
    claude_turn_text,
    codex_agent_message,
    codex_turn_usages,
    forwarded,
    stream_events,
    stream_object,
)
from .runner_usage import (
    Usage,
    claude_json_usage,
    claude_stream_usage,
    claude_turn_usage,
    codex_jsonl_usage,
    codex_turn_usage,
    floor_usage,
)

PROMPT_PLACEHOLDER = "{prompt}"

MODEL_PLACEHOLDER = "{model}"

HEADLESS = "headless"
HANDOFF = "handoff"

HELP_FLAG = "--help"

PROMPT_VIA = ("arg", "stdin")

AGENT_NAME_FLAG = "agent-name"
AGENT_STYLES = (AGENT_NAME_FLAG,)

RESUME_FORK_FLAGS = "resume-fork"
RESUME_STYLES = (RESUME_FORK_FLAGS,)

LOST_SESSION_MARKER = "No conversation found with session ID"

DENY_TOOL_FLAG = "deny-tool"
DISALLOWED_TOOLS_FLAG = "disallowed-tools"
DENY_STYLES = (DENY_TOOL_FLAG, DISALLOWED_TOOLS_FLAG)

MANUAL_RUNNER = "manual"

AUTO = "auto"
AUTO_ORDER = ("claude", "codex", "copilot")

USAGE_FORMATS = (CLAUDE_JSON, CLAUDE_STREAM_JSON, CODEX_JSONL, COPILOT_SESSION_STORE)

_USAGE_FLAGS = {
    CLAUDE_JSON: ("--output-format", "json"),
    CLAUDE_STREAM_JSON: (
        "--output-format",
        "stream-json",
        "--verbose",
        "--forward-subagent-text",
    ),
    CODEX_JSONL: ("--json",),
    COPILOT_SESSION_STORE: ("--session-id",),
}


@dataclass(frozen=True)
class RunnerSpec:
    name: str
    kind: str = HEADLESS
    command: tuple[str, ...] = ()
    prompt_via: str = "arg"
    model: str | None = None
    tier: str | None = None
    vendor: str | None = None
    tier_source: str | None = None
    deny_tools: tuple[str, ...] = ()
    deny_style: str | None = None
    agent_style: str | None = None
    resume_style: str | None = None
    sandbox: str | None = None
    approval: str | None = None
    git_name: str | None = None
    git_email: str | None = None
    usage_format: str | None = None
    session_store: Path | None = None
    context_window: int = DEFAULT_CONTEXT_WINDOW
    context_window_source: str | None = None

    @property
    def binary(self) -> str | None:
        return self.command[0] if self.command else None


BUILTIN_RUNNERS: tuple[RunnerSpec, ...] = (
    RunnerSpec(
        "claude",
        HEADLESS,
        ("claude", "-p", PROMPT_PLACEHOLDER),
        deny_style=DISALLOWED_TOOLS_FLAG,
        agent_style=AGENT_NAME_FLAG,
        resume_style=RESUME_FORK_FLAGS,
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
        context_window_source=FALLBACK_WINDOW,
    ),
    RunnerSpec(MANUAL_RUNNER, HANDOFF, context_window_source=FALLBACK_WINDOW),
)


@dataclass(frozen=True)
class RunResult:
    runner: str
    command: tuple[str, ...]
    executed: bool
    returncode: int | None = None
    stdout: str = ""
    stderr: str = ""
    handoff: bool = False
    duration_s: float | None = None
    timed_out: bool = False
    stopped: StopReason | None = None
    session_id: str | None = None
    model_resolution: models.ModelResolution | None = None
    seed: SessionSeed | None = None


def format_command(  # noqa: PLR0913 — an argv fact each; a bundle hides which are optional
    spec: RunnerSpec,
    prompt: str,
    *,
    capture_usage: bool = False,
    session_id: str | None = None,
    role: str | None = None,
    seed: SessionSeed | None = None,
) -> list[str]:

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
    argv = _apply_seed(spec, _apply_deny_tools(spec, argv), seed)
    argv = _apply_role(spec, _apply_model(spec, _apply_sandbox(spec, argv)), role)
    return _apply_usage(spec, argv, session_id) if capture_usage else argv


def _apply_usage(spec: RunnerSpec, argv: list[str], session_id: str | None) -> list[str]:

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


AGENT_MODEL_PIN = "agent model pin"
AGENT_TIER = "agent tier"
FAMILY_DEFAULT_TIER = "family default tier"


def model_family(spec: RunnerSpec) -> str:

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

    if role is None or spec.agent_style is None:
        return argv
    if spec.agent_style != AGENT_NAME_FLAG:
        raise ValueError(
            f"runner {spec.name!r} has agent_style {spec.agent_style!r}; "
            f"known: {list(AGENT_STYLES)}"
        )
    return [argv[0], "--agent", role, *argv[1:]]


@dataclass(frozen=True)
class SessionSeed:
    session_id: str
    exists: bool


SESSION_SEEDS_FILE = run_record.USAGE_DIR / "session-seeds.json"

_SEED_LOCK = threading.Lock()


def _read_seeds(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except OSError, ValueError:
        return {}
    return data if isinstance(data, dict) else {}


def session_seed(repo_root: Path, feature_id: str, family: str) -> SessionSeed:

    known = _read_seeds(repo_root / SESSION_SEEDS_FILE).get(feature_id)
    if isinstance(known, dict) and isinstance(known.get(family), str) and known[family]:
        return SessionSeed(known[family], exists=True)
    return SessionSeed(str(uuid.uuid4()), exists=False)


def record_session_seed(repo_root: Path, feature_id: str, family: str, session_id: str) -> None:
    path = repo_root / SESSION_SEEDS_FILE
    path.parent.mkdir(parents=True, exist_ok=True)
    gitignore = path.parent / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")
    with _SEED_LOCK:
        data = _read_seeds(path)
        entry = data.get(feature_id)
        if not isinstance(entry, dict):
            entry = {}
            data[feature_id] = entry
        entry[family] = session_id
        tmp = path.with_suffix(f".{os.getpid()}.json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(path)


def _apply_seed(spec: RunnerSpec, argv: list[str], seed: SessionSeed | None) -> list[str]:

    if seed is None or spec.resume_style is None:
        return argv
    if spec.resume_style != RESUME_FORK_FLAGS:
        raise ValueError(
            f"runner {spec.name!r} has resume_style {spec.resume_style!r}; "
            f"known: {list(RESUME_STYLES)}"
        )
    if seed.exists:
        return [argv[0], "--resume", seed.session_id, "--fork-session", *argv[1:]]
    return [argv[0], "--session-id", seed.session_id, *argv[1:]]


def _apply_deny_tools(spec: RunnerSpec, argv: list[str]) -> list[str]:

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

    flags: list[str] = []
    if spec.sandbox is not None:
        flags += ["--sandbox", spec.sandbox]
    if spec.approval is not None:
        flags += ["-a", spec.approval]
    if not flags:
        return argv
    return [argv[0], *flags, *argv[1:]]


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

_DECIDER_DENY_COPILOT: tuple[str, ...] = ("shell", "write", "read")


def confine_for_decider(spec: RunnerSpec) -> RunnerSpec | None:

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
    return (*spec.deny_tools, *(t for t in extra if t not in spec.deny_tools))


def is_available(spec: RunnerSpec, *, which: Callable[[str], str | None] | None = None) -> bool:
    which = which or shutil.which
    if spec.kind == HANDOFF:
        return True
    return spec.binary is not None and which(spec.binary) is not None


@dataclass(frozen=True)
class Capability:
    reachable: bool
    flag_ok: bool
    detail: str


def _headless_flags(spec: RunnerSpec) -> list[str]:
    return [t for t in spec.command[1:] if t not in (PROMPT_PLACEHOLDER, MODEL_PLACEHOLDER)]


def _run_help(binary: str) -> str | None:
    try:
        proc = subprocess.run(  # noqa: S603 — configured binary, literal argv
            [binary, HELP_FLAG], capture_output=True, text=True, check=False, timeout=10
        )
    except OSError, subprocess.SubprocessError:
        return None
    return (proc.stdout or "") + (proc.stderr or "")


_GUARDRAIL_FLAGS: tuple[tuple[str, str], ...] = (
    ("sandbox", "--sandbox"),
    ("approval", "--ask-for-approval"),
)

_INLINE_VALUES = re.compile(r"\[possible values:\s*([^\]]*)\]", re.IGNORECASE)
_VALUES_HEADING = re.compile(r"^\s*possible values:\s*$", re.IGNORECASE | re.MULTILINE)
_VALUE_BULLET = re.compile(r"^\s*-\s+([A-Za-z0-9][\w-]*)\s*(?::|$)")
_NEXT_OPTION = re.compile(r"^(\s*)(?:-[A-Za-z0-9], --[\w-]+|--[\w-]+)")


def possible_values(help_text: str, flag: str) -> tuple[str, ...] | None:

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
    match = _NEXT_OPTION.match(line)
    return match is not None and flag in re.split(r"[,\s=<]+", line.strip())


def _values_in(slice_text: str) -> tuple[str, ...] | None:
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
    if spec.kind != HEADLESS or spec.binary is None:
        return ()
    return check_guardrails(spec, help_text=(run or _run_help)(spec.binary))


def probe_capability(
    spec: RunnerSpec, *, run: Callable[[str], str | None] | None = None
) -> Capability:

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
    return is_available(spec, which=which) and probe_capability(spec, run=run).flag_ok


def select_runner(
    specs: tuple[RunnerSpec, ...],
    chosen: str | None = None,
    *,
    which: Callable[[str], str | None] | None = None,
    capable: Callable[[RunnerSpec], bool] | None = None,
) -> RunnerSpec:

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

    if spec.git_name is None or spec.git_email is None:
        return None
    return {
        "GIT_AUTHOR_NAME": spec.git_name,
        "GIT_AUTHOR_EMAIL": spec.git_email,
        "GIT_COMMITTER_NAME": spec.git_name,
        "GIT_COMMITTER_EMAIL": spec.git_email,
    }


def br_attribution_env(spec: RunnerSpec) -> dict[str, str]:

    env = {"BR_AGENT_NAME": spec.name, "BR_HARNESS": "basicly-loop"}
    if spec.model is not None:
        env["BR_MODEL"] = spec.model
    return env


PROJECT_ENV_VAR = "VIRTUAL_ENV"


def sanitised_project_env(env: Mapping[str, str], cwd: Path | str | None) -> dict[str, str]:

    venv = env.get(PROJECT_ENV_VAR)
    if not venv:
        return dict(env)
    here = (Path.cwd() if cwd is None else Path(cwd)).resolve()
    if here.is_relative_to(Path(venv).resolve().parent):
        return dict(env)
    return {name: value for name, value in env.items() if name != PROJECT_ENV_VAR}


def dispatch_env(
    spec: RunnerSpec, base: Mapping[str, str], cwd: Path | str | None
) -> dict[str, str]:

    identity = git_identity_env(spec)
    scrubbed = sanitised_project_env(sanitised_colour_env(sanitised_git_env(base)), cwd)
    return {**scrubbed, **br_attribution_env(spec), **(identity or {})}


KILL_GRACE_S = 5.0

CREATE_NEW_PROCESS_GROUP = getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0x00000200)

SIGKILL = getattr(signal, "SIGKILL", 9)


def _kill_tree(proc: subprocess.Popen[str]) -> None:

    if os.name == "nt":
        _taskkill_tree(proc.pid)
        return
    for signum in (signal.SIGTERM, SIGKILL):
        try:
            os.killpg(os.getpgid(proc.pid), signum)
        except OSError:
            return
        if signum == SIGKILL:
            return
        try:
            proc.wait(timeout=KILL_GRACE_S)
        except subprocess.TimeoutExpired:
            continue
        else:
            return


def _taskkill_tree(pid: int) -> None:
    try:
        subprocess.run(  # noqa: S603 — fixed argv, no shell
            ["taskkill", "/F", "/T", "/PID", str(pid)],  # noqa: S607 — a Windows system tool,
            capture_output=True,
            check=False,
            timeout=KILL_GRACE_S,
        )
    except OSError, subprocess.SubprocessError:
        return


def _drain(proc: subprocess.Popen[str]) -> tuple[str, str]:

    try:
        return proc.communicate(timeout=KILL_GRACE_S)
    except subprocess.TimeoutExpired, ValueError:
        return "", ""


STREAMING_FORMATS = (CLAUDE_STREAM_JSON, CODEX_JSONL)

STREAM_ERRORS = "replace"

READER_JOIN_S = KILL_GRACE_S


@dataclass(frozen=True)
class StreamEvent:
    line: str
    data: dict | None = None
    usage: Usage | None = None
    text: str | None = None
    subagent: str | None = None
    tools: tuple[str, ...] = ()


EventSink = Callable[[StreamEvent], object]


SPEND_BOUND = "spend"
QUIET_BOUND = "quiet"

DEFAULT_QUIET_AFTER = 1800.0

STOP_POLL_S = 0.5


@dataclass(frozen=True)
class StopReason:
    bound: str
    detail: str


@dataclass(frozen=True)
class DispatchBounds:
    quiet_after: float | None = None
    token_ceiling: int | None = None

    @property
    def armed(self) -> bool:
        return self.quiet_after is not None or self.token_ceiling is not None

    def interval(self) -> float:

        if self.quiet_after is None:
            return STOP_POLL_S
        return max(0.01, min(STOP_POLL_S, self.quiet_after / 4))


def stop_label(result: RunResult, timeout: float) -> str:

    if result.stopped is not None:
        return f"{result.stopped.bound} bound: {result.stopped.detail}"
    return f"runner_timeout after {timeout:.0f}s"


def _streaming(spec: RunnerSpec, *, capture_usage: bool) -> bool:
    return capture_usage and spec.usage_format in STREAMING_FORMATS


def _emit(spec: RunnerSpec, line: str, on_event: EventSink) -> int:

    text = redact_secrets(line.rstrip("\n"))
    data = stream_object(text)
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
    return event.usage.tokens if event.usage is not None else 0


def _pump(stream: IO[str], on_line: Callable[[str], object]) -> None:

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
    stdout: str
    stderr: str
    returncode: int | None
    timed_out: bool
    stopped: StopReason | None = None


class _Liveness:
    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._at = time.monotonic()
        self._tokens = 0

    def stamp(self, tokens: int = 0) -> None:
        with self._lock:
            self._at = time.monotonic()
            self._tokens += tokens

    def tokens(self) -> int:
        with self._lock:
            return self._tokens

    def quiet_for(self) -> float:
        with self._lock:
            return time.monotonic() - self._at


def _bound_reached(bounds: DispatchBounds, liveness: _Liveness) -> StopReason | None:

    if bounds.quiet_after is not None and liveness.quiet_for() >= bounds.quiet_after:
        return StopReason(QUIET_BOUND, f"no stream events for {bounds.quiet_after:g}s")
    spent = liveness.tokens()
    if bounds.token_ceiling is not None and spent >= bounds.token_ceiling:
        return StopReason(
            SPEND_BOUND, f"{spent} tokens reported against a {bounds.token_ceiling} lane ceiling"
        )
    return None


def _read_streaming(  # noqa: PLR0913 — one parameter per independent read input
    proc: subprocess.Popen[str],
    spec: RunnerSpec,
    stdin: str | None,
    timeout: float | None,
    on_event: EventSink,
    bounds: DispatchBounds | None = None,
) -> _Streamed:

    out_lines: list[str] = []
    err_lines: list[str] = []
    liveness = _Liveness()

    def observe(line: str) -> None:
        out_lines.append(line)
        liveness.stamp(_emit(spec, line, on_event))

    readers = (
        threading.Thread(target=_pump, args=(proc.stdout, observe), daemon=True),
        threading.Thread(target=_pump, args=(proc.stderr, err_lines.append), daemon=True),
    )
    for reader in readers:
        reader.start()
    if stdin is not None and proc.stdin is not None:
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
            continue
        reason = _bound_reached(bounds, liveness)
        if reason is not None:
            _kill_tree(proc)
            return _Streamed("", "", None, timed_out=True, stopped=reason)


def _process_isolation(os_name: str) -> tuple[bool, int]:
    return os_name != "nt", CREATE_NEW_PROCESS_GROUP if os_name == "nt" else 0


def _resolved(argv: list[str], env: Mapping[str, str]) -> list[str]:
    return [shutil.which(argv[0], path=env.get("PATH")) or argv[0], *argv[1:]]


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
    seed: SessionSeed | None = None,
) -> RunResult:

    resolution = resolve_model(spec, repo_root=cwd)
    carried = resolution if (resolution.model or resolution.tier) else None
    if resolution.model is not None:
        spec = replace(spec, model=resolution.model)
    if spec.kind == HANDOFF:
        return RunResult(spec.name, (), executed=False, handoff=True, model_resolution=carried)
    session_id = (
        str(uuid.uuid4()) if capture_usage and spec.usage_format == COPILOT_SESSION_STORE else None
    )
    argv = format_command(
        spec, prompt, capture_usage=capture_usage, session_id=session_id, role=role, seed=seed
    )
    if dry_run:
        return RunResult(
            spec.name,
            tuple(argv),
            executed=False,
            session_id=session_id,
            model_resolution=carried,
            seed=seed,
        )
    stdin = prompt if spec.prompt_via == "stdin" else None
    stdin_source = subprocess.PIPE if stdin is not None else subprocess.DEVNULL
    env = dispatch_env(spec, os.environ, cwd)
    start = time.perf_counter()
    timed_out = False
    stopped: StopReason | None = None
    sink = on_event if _streaming(spec, capture_usage=capture_usage) else None
    new_session, creationflags = _process_isolation(os.name)
    proc = subprocess.Popen(  # noqa: S603 — argv is the engine-built spec, no shell
        _resolved(argv, env),
        cwd=cwd,
        stdin=stdin_source,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        errors=STREAM_ERRORS if sink is not None else None,
        env=env,
        start_new_session=new_session,
        creationflags=creationflags,
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
        _kill_tree(proc)
        if sink is None:
            _drain(proc)
        raise
    duration_s = time.perf_counter() - start
    result = RunResult(
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
        seed=seed,
    )
    if not _seed_was_lost(seed, result):
        return result
    return run(
        spec,
        prompt,
        cwd,
        capture_usage=capture_usage,
        timeout=timeout,
        on_event=on_event,
        bounds=bounds,
        role=role,
        seed=SessionSeed(str(uuid.uuid4()), exists=False),
    )


def _seed_was_lost(seed: SessionSeed | None, result: RunResult) -> bool:
    return (
        seed is not None
        and seed.exists
        and not result.timed_out
        and result.returncode != 0
        and LOST_SESSION_MARKER in result.stderr
    )


def extract_usage(spec: RunnerSpec, result: RunResult) -> Usage | None:

    if result.handoff:
        return None
    if not result.executed:
        captured = result.stdout or result.stderr
        return floor_usage(result.stdout, result.stderr) if captured else None
    reported: Usage | None = None
    if spec.usage_format == CLAUDE_JSON:
        reported = claude_json_usage(result.stdout)
    elif spec.usage_format == CLAUDE_STREAM_JSON:
        reported = claude_json_usage(claude_result_event(result.stdout)) or claude_stream_usage(
            result.stdout
        )
    elif spec.usage_format == CODEX_JSONL:
        reported = codex_jsonl_usage(result.stdout)
    elif spec.usage_format == COPILOT_SESSION_STORE:
        reported = store_usage(spec, result.session_id)
    if reported is not None:
        return reported
    return floor_usage(result.stdout, result.stderr)


def result_text(spec: RunnerSpec, stdout: str) -> str:

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

    if spec.usage_format == CLAUDE_STREAM_JSON:
        return claude_turn_usage(event)
    if spec.usage_format == CODEX_JSONL:
        return codex_turn_usage(event)
    return None


def event_text(spec: RunnerSpec, event: dict) -> str | None:

    if spec.usage_format != CLAUDE_STREAM_JSON:
        return None
    message = event.get("message")
    if not isinstance(message, dict):
        return None
    return claude_turn_text(message) or None


def event_tools(spec: RunnerSpec, event: dict) -> tuple[str, ...]:

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

    if spec.usage_format != CLAUDE_STREAM_JSON or not forwarded(event):
        return None
    kind = event.get(CLAUDE_SUBAGENT_TYPE)
    return kind if isinstance(kind, str) and kind else UNNAMED_SUBAGENT


def observed_models(spec: RunnerSpec, result: RunResult) -> tuple[str, ...]:

    if not result.executed:
        return ()
    if spec.usage_format in (CLAUDE_JSON, CLAUDE_STREAM_JSON):
        streaming = spec.usage_format == CLAUDE_STREAM_JSON
        return _claude_observed_models(result.stdout, streaming=streaming)
    if spec.usage_format == COPILOT_SESSION_STORE:
        return _copilot_observed_models(spec, result.session_id)
    return ()


def _dedup(names: list[str]) -> tuple[str, ...]:
    seen: dict[str, None] = {}
    for name in names:
        if isinstance(name, str) and name:
            seen.setdefault(name, None)
    return tuple(seen)


def _claude_observed_models(stdout: str, *, streaming: bool) -> tuple[str, ...]:
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
    data = shutdown_data(spec, session_id)
    metrics = data.get("modelMetrics") if data is not None else None
    if not isinstance(metrics, dict):
        return ()
    return _dedup(list(metrics))


def model_mismatch(pinned: str | None, observed: tuple[str, ...]) -> str | None:

    if pinned is None or not observed:
        return None
    if any(models.same_model(pinned, seen) for seen in observed):
        return None
    return f"pinned {pinned!r} but the adapter reported {', '.join(repr(o) for o in observed)}"


def context_occupancy(spec: RunnerSpec, result: RunResult) -> int | None:

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

    if spec.name in _VERSION_CACHE:
        return _VERSION_CACHE[spec.name]
    version: str | None = None
    executable = spec.command[0] if spec.command else None
    if executable and shutil.which(executable):
        with contextlib.suppress(OSError, subprocess.SubprocessError):
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

    command: tuple[str, ...] = ()
    if prompt is not None:
        command = tuple(
            run_record.REDACTED_PROMPT if arg == prompt else arg for arg in result.command
        )
    usage = extract_usage(spec, result)
    digest = hashlib.sha256(prompt.encode("utf-8")).hexdigest() if prompt is not None else None
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
        stopped_bound=stopped_bound,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        forecast_spend_tokens=forecast_spend_tokens,
        context_tokens=context_occupancy(spec, result),
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
    with contextlib.suppress(OSError, RuntimeError):
        run_record.record_marker(repo_root, issue_id, entry)


LANE = "lane"
DECIDER = "decider"
HELPER = "helper"
PROCESS_CLASSES = (LANE, DECIDER, HELPER)

DECIDER_SLOTS = 1

DEFAULT_MAX_AGENT_PROCESSES = 8

DEFAULT_STALL_AFTER = 900.0


class BudgetExhaustedError(RuntimeError):
    pass


class ProcessBudget:
    def __init__(self, total: int, lane_slots: int) -> None:
        self.total = max(DECIDER_SLOTS + 1, total)
        self.decider_slots = min(DECIDER_SLOTS, self.total)
        self.lane_slots = max(1, min(lane_slots, self.total - self.decider_slots))
        self.helper_slots = max(0, self.total - self.lane_slots - self.decider_slots)
        self._live: dict[str, int] = dict.fromkeys(PROCESS_CLASSES, 0)
        self._lock = threading.Lock()
        self._freed = threading.Condition(self._lock)

    def capacity(self, kind: str) -> int:
        if kind == LANE:
            return self.lane_slots
        if kind == DECIDER:
            return self.decider_slots
        if kind == HELPER:
            return self.helper_slots
        raise ValueError(f"unknown process class {kind!r}; expected one of {PROCESS_CLASSES}")

    def live(self, kind: str) -> int:
        self.capacity(kind)
        with self._lock:
            return self._live[kind]

    @contextlib.contextmanager
    def slot(self, kind: str, *, timeout: float | None = None):

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
_BUDGET: dict[str, ProcessBudget] = {}


def configure_process_budget(total: int, lane_slots: int) -> ProcessBudget:

    with _BUDGET_LOCK:
        return _BUDGET.setdefault("current", ProcessBudget(total, lane_slots))


def process_budget() -> ProcessBudget:

    total = DEFAULT_MAX_AGENT_PROCESSES
    return configure_process_budget(total, max(1, total // 2))


def reset_process_budget() -> None:
    with _BUDGET_LOCK:
        _BUDGET.clear()


class StallWatchdog:
    def __init__(
        self,
        after: float,
        probe: Callable[[], str],
        on_stall: Callable[[], object],
        *,
        poll: float | None = None,
    ) -> None:
        self.after = after
        self._probe = probe
        self._on_stall = on_stall
        self._poll = poll if poll is not None else max(0.01, after / 4)
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self.flagged = False

    def _fingerprint(self) -> str:

        try:
            return self._probe()
        except OSError, RuntimeError, ValueError:
            return "<probe-failed>"

    def _watch(self) -> None:
        last = self._fingerprint()
        quiet_since = time.monotonic()
        while not self._stop.wait(self._poll):
            current = self._fingerprint()
            if current != last:
                last, quiet_since = current, time.monotonic()
                continue
            if self.flagged or time.monotonic() - quiet_since < self.after:
                continue
            self.flagged = True
            with contextlib.suppress(Exception):
                self._on_stall()

    def start(self) -> None:
        self._thread = threading.Thread(target=self._watch, daemon=True)
        self._thread.start()

    def stop(self, timeout: float = 5.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout)
            self._thread = None

    def __enter__(self) -> StallWatchdog:
        self.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self.stop()

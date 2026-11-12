from __future__ import annotations

import dataclasses
import re
from collections.abc import Mapping

from basicly import runner

EFFORT_LEVELS = ("low", "medium", "high", "xhigh", "max")
MODEL_ALIASES = frozenset({"opus", "sonnet", "haiku", "fable", "default", "inherit", "opusplan"})
MODERN_CLAUDE = re.compile(
    r"claude-(?:fable-5(?:-[12])?|(?:opus|sonnet)-5(?:-5)?|opus-4-[78]|haiku-5-5)(?:\[1m\])?"
)


def validate_settings(family: str, model: str, effort: str) -> None:
    if family not in {"claude", "codex"}:
        raise ValueError("comparison family must be claude or codex")
    if model.removesuffix("[1m]") in MODEL_ALIASES or not re.fullmatch(
        r"[A-Za-z0-9][A-Za-z0-9_.:/\[\]-]{0,127}", model
    ):
        raise ValueError("comparisons require a versioned model id, not a model alias")
    if effort not in EFFORT_LEVELS:
        raise ValueError(f"unknown effort {effort!r}; choose from {', '.join(EFFORT_LEVELS)}")
    if effort in {"xhigh", "max"}:
        allowed = (
            bool(MODERN_CLAUDE.fullmatch(model))
            or (effort == "max" and model in {"claude-opus-4-6", "claude-sonnet-4-6"})
            if family == "claude"
            else model in {"gpt-6-luna", "gpt-6.1-terra", "gpt-6.1-sol", "gpt-6-astra"}
        )
        if not allowed:
            raise ValueError(f"effort {effort!r} is not reviewed for model {model!r} on {family}")


def make_spec(
    family: str, model: str, effort: str, advisor_model: str | None = None
) -> runner.RunnerSpec:
    validate_settings(family, model, effort)
    spec = next(spec for spec in runner.BUILTIN_RUNNERS if spec.name == family)
    if family == "codex":
        if advisor_model is not None:
            raise ValueError("advisor comparisons require the claude family")
        command = (*spec.command, "-c", f'model_reasoning_effort="{effort}"')
        return dataclasses.replace(
            spec, command=command, model=model, sandbox="workspace-write", approval="never"
        )
    command = (*spec.command, "--effort", effort)
    if advisor_model not in {None, "off"}:
        if advisor_model not in {"claude-fable-5-1", "claude-fable-5-2"}:
            raise ValueError("advisor comparisons require a reviewed versioned Fable model")
        if model == "claude-fable-5-2" and advisor_model != model:
            raise ValueError("a Fable advisor cannot be older than its owner model")
        command = (*command, "--advisor", advisor_model)
    return dataclasses.replace(spec, command=command, model=model)


def validate_environment(
    family: str, effort: str, advisor_model: str | None, environment: Mapping[str, str]
) -> None:
    if family != "claude":
        return
    inherited = environment.get("CLAUDE_CODE_EFFORT_LEVEL")
    if inherited and inherited != effort:
        raise ValueError("CLAUDE_CODE_EFFORT_LEVEL overrides the requested comparison effort")
    disabled = environment.get("CLAUDE_CODE_DISABLE_ADVISOR_TOOL") == "1"
    if advisor_model == "off" and not disabled:
        raise ValueError("set CLAUDE_CODE_DISABLE_ADVISOR_TOOL=1 for an advisor-off comparison")
    if advisor_model not in {None, "off"} and disabled:
        raise ValueError("CLAUDE_CODE_DISABLE_ADVISOR_TOOL disables the requested advisor")

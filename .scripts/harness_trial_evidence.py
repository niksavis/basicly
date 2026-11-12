from __future__ import annotations

import dataclasses
from pathlib import Path
from typing import Any

from harness_trial_arms import verify_value_loading
from harness_trial_protocols import observed_session

from basicly import runner


def owner_models(spec: runner.RunnerSpec, result: runner.RunResult) -> tuple[str, ...]:
    if spec.usage_format != runner.CLAUDE_STREAM_JSON:
        return ()
    found: dict[str, None] = {}
    for event in runner.stream_events(result.stdout):
        message = event.get("message")
        if (
            event.get("type") != "assistant"
            or runner.forwarded(event)
            or not isinstance(message, dict)
        ):
            continue
        model = message.get("model")
        if isinstance(model, str) and model:
            found[model] = None
    return tuple(found)


def save_execution(
    spec: runner.RunnerSpec,
    result: runner.RunResult,
    directory: Path,
    report: dict[str, Any],
) -> None:
    (directory / "agent.stdout").write_text(result.stdout, encoding="utf-8")
    (directory / "agent.stderr").write_text(result.stderr, encoding="utf-8")
    report.update(agent_duration_s=result.duration_s, returncode=result.returncode)
    if stages := report.get("stages"):
        times = [stage["duration_s"] for stage in stages]
        report["agent_duration_s"] = (
            sum(times) if all(value is not None for value in times) else None
        )
    if error := report.get("protocol", {}).get("error"):
        raise ValueError(error)
    report["session_id"] = observed_session(spec, result)
    verify_value_loading(spec, directory / "workspace", report)
    resolution = result.model_resolution
    report["model_resolution"] = dataclasses.asdict(resolution) if resolution else None
    observed = runner.observed_models(spec, result)
    owner = owner_models(spec, result)
    pinned = spec.model or report.get("model")
    report["observed_models"] = list(observed)
    report["owner_models"] = list(owner)
    mismatch = runner.model_mismatch(pinned, observed)
    if owner:
        mismatch = next(
            (reason for model in owner if (reason := runner.model_mismatch(pinned, (model,)))),
            None,
        )
    report["model_mismatch"] = mismatch
    report["model_verified"] = (bool(owner) or len(observed) == 1) and mismatch is None
    usage = runner.extract_usage(spec, result)
    report["usage"] = dataclasses.asdict(usage) if usage is not None else None
    if usage is not None and not usage.estimated:
        report.update(tokens=usage.tokens, cost=usage.cost)
    report["cost_source"] = "client-side CLI estimate" if report["cost"] is not None else "unknown"
    if report.get("protocol", {}).get("kind") == "resume":
        report.update(tokens=None, cost=None, cost_source="unreconciled multi-stage usage")
    if report["model_mismatch"]:
        raise ValueError(report["model_mismatch"])

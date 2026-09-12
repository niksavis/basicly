from __future__ import annotations

import contextlib
import hashlib
import json
import os
import statistics
import threading
from collections.abc import Iterable, Mapping
from dataclasses import asdict, dataclass, fields
from datetime import UTC, datetime
from pathlib import Path

from . import dispatch_phase, session, spend_calibration, tracker

USAGE_DIR = Path(".basicly/usage")
RUN_RECORDS_FILE = USAGE_DIR / "run-records.json"

REDACTED_PROMPT = "<prompt-redacted>"

EXECUTED = "executed"
FAILED = "failed"
HANDOFF = "handoff"
UNSTARTED = "unstarted"


BUILD_PHASE = dispatch_phase.BUILD_PHASE
LANE_PHASE = dispatch_phase.LANE_PHASE
VALIDATE_PHASE = dispatch_phase.VALIDATE_PHASE
DECIDE_PHASE = dispatch_phase.DECIDE_PHASE
PROPOSE_PHASE = dispatch_phase.PROPOSE_PHASE
WRITE_PHASES = dispatch_phase.WRITE_PHASES
is_write_phase = dispatch_phase.is_write_phase

PRIOR_RATIO = spend_calibration.PRIOR_RATIO
MEASURED_RATIO = spend_calibration.MEASURED_RATIO
UNDECLARED_RATIO = spend_calibration.UNDECLARED_RATIO
SpendPrior = spend_calibration.SpendPrior
DECLARED_SPEND_PRIOR = spend_calibration.DECLARED_SPEND_PRIOR
CalibratedRatio = spend_calibration.CalibratedRatio
SpendCalibration = spend_calibration.SpendCalibration
spend_samples = spend_calibration.spend_samples
calibrate_spend = spend_calibration.calibrate_spend


@dataclass(frozen=True)
class RunRecord:
    agent: str
    outcome: str
    returncode: int | None
    duration_s: float | None
    command: tuple[str, ...]
    timestamp: str
    model: str | None = None
    model_tier: str | None = None
    model_source: str | None = None
    tier_honoured: bool | None = None
    observed_models: tuple[str, ...] = ()
    model_mismatch: str | None = None
    tokens: int | None = None
    cost: float | None = None
    estimated: bool | None = None
    input_tokens: int | None = None
    output_tokens: int | None = None
    cache_read_tokens: int | None = None
    cache_write_tokens: int | None = None
    reasoning_tokens: int | None = None
    credits: float | None = None
    adapter_version: str | None = None
    prompt_sha256: str | None = None
    phase: str | None = None
    stopped_bound: str | None = None
    scope_tokens: int | None = None
    forecast_tokens: int | None = None
    forecast_spend_tokens: int | None = None
    context_tokens: int | None = None
    context_window: int | None = None
    context_window_source: str | None = None
    task_class: str | None = None
    forecast_source: str | None = None
    build_factor_source: str | None = None
    folded_info: tuple[str, ...] = ()
    config_overrides: tuple[str, ...] = ()
    dispatch_rank: int | None = None
    scheduler_rank: int | None = None
    scheduler_fallback_rank: int | None = None
    scheduler_score: int | None = None
    scheduler_policy: str | None = None


def outcome_of(*, handoff: bool, returncode: int | None, started: bool = True) -> str:

    if handoff:
        return HANDOFF
    if not started:
        return UNSTARTED
    return EXECUTED if returncode == 0 else FAILED


def build_record(  # noqa: PLR0913
    *,
    agent: str,
    handoff: bool,
    returncode: int | None,
    duration_s: float | None,
    command: tuple[str, ...],
    started: bool = True,
    model: str | None = None,
    model_tier: str | None = None,
    model_source: str | None = None,
    tier_honoured: bool | None = None,
    observed_models: tuple[str, ...] = (),
    model_mismatch: str | None = None,
    tokens: int | None = None,
    cost: float | None = None,
    estimated: bool | None = None,
    input_tokens: int | None = None,
    output_tokens: int | None = None,
    cache_read_tokens: int | None = None,
    cache_write_tokens: int | None = None,
    reasoning_tokens: int | None = None,
    credits: float | None = None,  # noqa: A002 — the field it feeds is `credits`; renaming
    adapter_version: str | None = None,
    prompt_sha256: str | None = None,
    phase: str | None = None,
    stopped_bound: str | None = None,
    scope_tokens: int | None = None,
    forecast_tokens: int | None = None,
    forecast_spend_tokens: int | None = None,
    context_tokens: int | None = None,
    context_window: int | None = None,
    context_window_source: str | None = None,
    task_class: str | None = None,
    forecast_source: str | None = None,
    build_factor_source: str | None = None,
    folded_info: tuple[str, ...] = (),
    dispatch_rank: int | None = None,
    scheduler_rank: int | None = None,
    scheduler_fallback_rank: int | None = None,
    scheduler_score: int | None = None,
    scheduler_policy: str | None = None,
) -> RunRecord:

    return RunRecord(
        agent=agent,
        outcome=outcome_of(handoff=handoff, returncode=returncode, started=started),
        returncode=returncode,
        duration_s=duration_s,
        command=tuple(command),
        timestamp=datetime.now(UTC).isoformat(),
        model=model,
        model_tier=model_tier,
        model_source=model_source,
        tier_honoured=tier_honoured,
        observed_models=tuple(observed_models),
        model_mismatch=model_mismatch,
        tokens=tokens,
        cost=cost,
        estimated=estimated,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_write_tokens=cache_write_tokens,
        reasoning_tokens=reasoning_tokens,
        credits=credits,
        adapter_version=adapter_version,
        prompt_sha256=prompt_sha256,
        phase=phase,
        stopped_bound=stopped_bound,
        scope_tokens=scope_tokens,
        forecast_tokens=forecast_tokens,
        forecast_spend_tokens=forecast_spend_tokens,
        context_tokens=context_tokens,
        context_window=context_window,
        context_window_source=context_window_source,
        task_class=task_class,
        forecast_source=forecast_source,
        build_factor_source=build_factor_source,
        folded_info=tuple(folded_info),
        dispatch_rank=dispatch_rank,
        scheduler_rank=scheduler_rank,
        scheduler_fallback_rank=scheduler_fallback_rank,
        scheduler_score=scheduler_score,
        scheduler_policy=scheduler_policy,
        config_overrides=session.override_pairs(),
    )


_RECORD_LOCK = threading.Lock()


def record(repo_root: Path, bead_id: str, run_record: RunRecord) -> None:

    usage_dir = repo_root / USAGE_DIR
    usage_dir.mkdir(parents=True, exist_ok=True)
    gitignore = usage_dir / ".gitignore"
    if not gitignore.exists():
        gitignore.write_text("*\n", encoding="utf-8")

    records_file = repo_root / RUN_RECORDS_FILE
    with _RECORD_LOCK:
        data = _read(records_file)
        history = data.get(bead_id)
        if not isinstance(history, list):
            history = []
            data[bead_id] = history
        history.append(asdict(run_record))

        tmp = records_file.with_suffix(f".{os.getpid()}.json.tmp")
        tmp.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        tmp.replace(records_file)


def load_run_records(repo_root: Path) -> dict[str, list[dict]] | None:
    records_file = repo_root / RUN_RECORDS_FILE
    if not records_file.exists():
        return None
    return _read(records_file)


def latest_record(repo_root: Path, bead_id: str) -> RunRecord | None:

    data = load_run_records(repo_root)
    if not data:
        return None
    history = data.get(bead_id)
    if not isinstance(history, list) or not history or not isinstance(history[-1], dict):
        return None
    known = {f.name for f in fields(RunRecord)}
    kwargs = {k: v for k, v in history[-1].items() if k in known}
    try:
        return RunRecord(**kwargs)
    except TypeError:
        return None


def _read(records_file: Path) -> dict[str, list]:
    try:
        data = json.loads(records_file.read_text(encoding="utf-8"))
    except OSError, json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


MARKER = "[harness-run]"


def marker_id(bead_id: str, prompt_sha256: str, phase: str, attempt: int = 1) -> str:

    digest = hashlib.sha256(f"{phase}:{prompt_sha256}".encode()).hexdigest()[:10]
    suffix = digest if attempt == 1 else f"{digest}-{attempt}"
    return f"{bead_id}#run-{suffix}"


def _recorded_marker_ids(repo_root: Path, bead_id: str, marker: str = MARKER) -> set[str]:

    found: set[str] = set()
    for comment in tracker.try_read_comments(repo_root, bead_id):
        text = str(comment.get("text", ""))
        for line in text.splitlines():
            if line.startswith(f"{marker} id="):
                found.add(line[len(f"{marker} id=") :].split()[0])
    return found


def record_dispatch_event(repo_root: Path, bead_id: str, run_record: RunRecord) -> None:

    sample = spend_sample(asdict(run_record))
    measured = sample[0] if sample is not None and sample[1] == MEASURED else 0
    beside = {
        "tokens": run_record.tokens,
        "estimated": run_record.estimated,
        "model": run_record.model,
    }
    reading = {
        tracker.DISPATCH_SPEND_KEY: measured,
        "outcome": run_record.outcome,
        "at": run_record.timestamp,
        "agent": run_record.agent,
        "phase": run_record.phase or "dispatch",
        **{name: value for name, value in beside.items() if value is not None},
    }
    with contextlib.suppress(tracker.TrackerDivergenceError, OSError):
        tracker.add_dispatch(repo_root, bead_id, reading)


def record_marker(repo_root: Path, bead_id: str, run_record: RunRecord) -> str | None:

    record_dispatch_event(repo_root, bead_id, run_record)
    if not run_record.prompt_sha256:
        return None
    phase = run_record.phase or "dispatch"
    existing = _recorded_marker_ids(repo_root, bead_id)
    attempt = 1
    while marker_id(bead_id, run_record.prompt_sha256, phase, attempt) in existing:
        attempt += 1
    ident = marker_id(bead_id, run_record.prompt_sha256, phase, attempt)
    payload = {k: v for k, v in asdict(run_record).items() if v not in (None, (), [])}
    body = f"{MARKER} id={ident} phase={phase}\n{json.dumps(payload, sort_keys=True)}"
    if not tracker.try_add_comment(repo_root, bead_id, body):
        return None
    return ident


def marker_payloads(texts: Iterable[str], marker: str = MARKER) -> list[dict]:

    payloads: list[dict] = []
    for text in texts:
        head, _, body = text.strip().partition("\n")
        if not (head == marker or head.startswith(f"{marker} ")):
            continue
        try:
            payload = json.loads(body)
        except json.JSONDecodeError:
            continue
        if isinstance(payload, dict):
            payloads.append(payload)
    return payloads


def tracker_history(repo_root: Path) -> dict[str, list[dict]]:

    history: dict[str, list[dict]] = {}
    for bead_id, texts in tracker.all_comment_texts(repo_root).items():
        payloads = marker_payloads(texts)
        if payloads:
            history[bead_id] = payloads
    return history


def dispatch_history(repo_root: Path) -> dict[str, list[dict]]:

    history: dict[str, list[dict]] = {}
    seen: set[tuple[str, str]] = set()
    for source in (tracker_history(repo_root), load_run_records(repo_root) or {}):
        for bead_id, entries in source.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                stamp = entry.get("timestamp")
                if isinstance(stamp, str) and stamp:
                    key = (bead_id, stamp)
                    if key in seen:
                        continue
                    seen.add(key)
                history.setdefault(bead_id, []).append(entry)
    return history


COST_MARKER = "[harness-cost]"


@dataclass(frozen=True)
class CostRollup:
    dispatches: int
    tokens: int | None = None
    cost: float | None = None
    wall_clock_s: float | None = None
    rework: int | None = None
    estimated: bool = False


@dataclass(frozen=True)
class CostForecast:
    tokens: int | None = None
    cost: float | None = None
    wall_clock_s: float | None = None
    source: str | None = None


def _numbers(entries: Iterable[Mapping[str, object]], key: str) -> list[float]:
    values: list[float] = []
    for entry in entries:
        value = entry.get(key)
        if isinstance(value, bool) or not isinstance(value, (int, float)):
            continue
        values.append(float(value))
    return values


def cost_rollup(
    history: Iterable[Mapping[str, object]], *, rework: int | None = None
) -> CostRollup:
    entries = [entry for entry in history if isinstance(entry, Mapping)]
    tokens = _numbers(entries, "tokens")
    cost = _numbers(entries, "cost")
    duration = _numbers(entries, "duration_s")
    return CostRollup(
        dispatches=len(entries),
        tokens=round(sum(tokens)) if tokens else None,
        cost=sum(cost) if cost else None,
        wall_clock_s=sum(duration) if duration else None,
        rework=rework,
        estimated=any(entry.get("estimated") is True for entry in entries),
    )


def cost_marker_id(bead_id: str) -> str:
    return f"{bead_id}#cost"


def record_cost_marker(  # noqa: PLR0913
    repo_root: Path,
    bead_id: str,
    *,
    actual: CostRollup,
    forecast: CostForecast,
    task_class: str | None = None,
    scope_tokens: int | None = None,
) -> str | None:

    if cost_marker_id(bead_id) in _recorded_marker_ids(repo_root, bead_id, COST_MARKER):
        return None
    ident = cost_marker_id(bead_id)
    payload = {
        "bead": bead_id,
        "task_class": task_class,
        "scope_tokens": scope_tokens,
        "forecast": asdict(forecast),
        "actual": asdict(actual),
    }
    body = f"{COST_MARKER} id={ident}\n{json.dumps(payload, sort_keys=True)}"
    if not tracker.try_add_comment(repo_root, bead_id, body):
        return None
    return ident


@dataclass(frozen=True)
class LandedCost:
    packages: int
    tokens: int | None = None
    cost: float | None = None
    wall_clock_s: float | None = None

    def per_package(self, metric: str) -> float | None:
        total = getattr(self, metric)
        if total is None or not self.packages:
            return None
        return total / self.packages


def landed_package_cost(repo_root: Path) -> LandedCost:

    packages = 0
    actuals: list[dict] = []
    for texts in tracker.all_comment_texts(repo_root).values():
        rollups = marker_payloads(texts, COST_MARKER)
        if not rollups:
            continue
        packages += 1
        actual = rollups[0].get("actual")
        if isinstance(actual, dict):
            actuals.append(actual)
    tokens = _numbers(actuals, "tokens")
    cost = _numbers(actuals, "cost")
    wall_clock = _numbers(actuals, "wall_clock_s")
    return LandedCost(
        packages=packages,
        tokens=round(sum(tokens)) if tokens else None,
        cost=sum(cost) if cost else None,
        wall_clock_s=sum(wall_clock) if wall_clock else None,
    )


@dataclass(frozen=True)
class ForecastError:
    bead: str
    timestamp: str
    forecast_tokens: int
    actual_tokens: int
    task_class: str | None = None
    model: str | None = None
    forecast_source: str | None = None
    phase: str | None = None
    estimated: bool = False
    actual_cost: float | None = None
    actual_wall_clock_s: float | None = None

    @property
    def ratio(self) -> float:

        return self.actual_tokens / self.forecast_tokens

    @property
    def error_tokens(self) -> int:
        return self.actual_tokens - self.forecast_tokens


@dataclass(frozen=True)
class ForecastErrorReport:
    errors: tuple[ForecastError, ...] = ()
    forecast_only: int = 0
    actual_only: int = 0
    unmetered: int = 0

    @property
    def paired(self) -> int:
        return len(self.errors)

    @property
    def median_ratio(self) -> float | None:

        if not self.errors:
            return None
        return statistics.median(error.ratio for error in self.errors)

    def by_task_class(self) -> dict[str, tuple[ForecastError, ...]]:
        grouped: dict[str, list[ForecastError]] = {}
        for error in self.errors:
            if error.task_class:
                grouped.setdefault(error.task_class, []).append(error)
        return {name: tuple(items) for name, items in sorted(grouped.items())}


MEASURED = "measured"
UNMETERED = "unmetered"


def spend_sample(entry: Mapping[str, object]) -> tuple[int, str] | None:

    tokens = entry.get("tokens")
    if isinstance(tokens, bool) or not isinstance(tokens, int):
        return None
    if entry.get("estimated") is not True:
        return tokens, MEASURED
    return tokens, UNSTARTED if entry.get("outcome") == UNSTARTED else UNMETERED


def dispatch_label(bead_id: str, entry: Mapping[str, object]) -> str:
    model = _text(entry, "model") or _text(entry, "agent")
    return f"{bead_id} on {model}" if model else bead_id


def positive_int(entry: Mapping[str, object], key: str) -> int | None:

    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value <= 0:
        return None
    return value


def forecast_errors(repo_root: Path) -> ForecastErrorReport:

    errors: list[ForecastError] = []
    forecast_only = actual_only = unmetered = 0
    for bead_id, history in sorted(dispatch_history(repo_root).items()):
        for entry in history:
            if not isinstance(entry, Mapping):
                continue
            forecast = positive_int(entry, "forecast_tokens")
            actual = positive_int(entry, "tokens")
            if forecast is None or actual is None:
                if forecast is not None:
                    forecast_only += 1
                elif actual is not None:
                    actual_only += 1
                else:
                    unmetered += 1
                continue
            errors.append(
                ForecastError(
                    bead=bead_id,
                    timestamp=str(entry.get("timestamp", "")),
                    forecast_tokens=forecast,
                    actual_tokens=actual,
                    task_class=_text(entry, "task_class"),
                    model=_text(entry, "model"),
                    forecast_source=_text(entry, "forecast_source"),
                    phase=_text(entry, "phase"),
                    estimated=entry.get("estimated") is True,
                    actual_cost=_positive_float(entry, "cost"),
                    actual_wall_clock_s=_positive_float(entry, "duration_s"),
                )
            )
    return ForecastErrorReport(
        errors=tuple(sorted(errors, key=lambda error: (error.timestamp, error.bead))),
        forecast_only=forecast_only,
        actual_only=actual_only,
        unmetered=unmetered,
    )


def _text(entry: Mapping[str, object], key: str) -> str | None:
    value = entry.get(key)
    return value if isinstance(value, str) and value else None


def _positive_float(entry: Mapping[str, object], key: str) -> float | None:

    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)

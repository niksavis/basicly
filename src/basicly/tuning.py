from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from . import config, run_record

LOCAL = "local"
TRACKER = "tracker"
BOTH = "both"

MEASURED = "measured"
SEEDED = "seeded"
UNOBSERVED = "unobserved"

CEILING_QUANTILE = 0.9
FLOOR_QUANTILE = 0.1

BACKSTOP_HEADROOM = 2.0

REWORK_QUANTILE = CEILING_QUANTILE


@dataclass(frozen=True)
class Dispatch:
    bead: str
    timestamp: str
    source: str
    entry: Mapping[str, object]
    attempt: int = 0


def read_dispatches(repo_root: Path) -> tuple[Dispatch, ...]:

    seen: dict[tuple[str, str], Dispatch] = {}
    for source, corpus in (
        (TRACKER, run_record.tracker_history(repo_root)),
        (LOCAL, run_record.load_run_records(repo_root) or {}),
    ):
        for bead_id, entries in corpus.items():
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, Mapping):
                    continue
                stamp = entry.get("timestamp")
                if not (isinstance(stamp, str) and stamp):
                    continue
                key = (str(bead_id), stamp)
                found = seen.get(key)
                if found is not None:
                    seen[key] = Dispatch(found.bead, found.timestamp, BOTH, found.entry)
                    continue
                seen[key] = Dispatch(str(bead_id), stamp, source, entry)
    ordered = sorted(seen.values(), key=lambda item: (item.timestamp, item.bead))
    return tuple(_with_attempts(ordered))


def _with_attempts(ordered: Sequence[Dispatch]) -> list[Dispatch]:

    counts: dict[str, int] = {}
    numbered: list[Dispatch] = []
    for item in ordered:
        if not run_record.is_write_phase(item.entry.get("phase")):
            numbered.append(item)
            continue
        counts[item.bead] = counts.get(item.bead, 0) + 1
        numbered.append(
            Dispatch(item.bead, item.timestamp, item.source, item.entry, counts[item.bead])
        )
    return numbered


@dataclass(frozen=True)
class Observation:
    bead: str
    timestamp: str
    source: str
    outcome: str
    in_force: str
    value: float


@dataclass(frozen=True)
class ValueCohort:
    in_force: str
    samples: int
    outcomes: dict[str, int]
    sources: dict[str, int]


@dataclass(frozen=True)
class ParameterTuning:
    key: str
    unit: str
    in_force: float
    prior: float
    cohorts: tuple[ValueCohort, ...]
    observations: tuple[Observation, ...]
    status: str
    recommendation: float | None
    basis: str
    min_samples: int

    @property
    def samples(self) -> int:
        return len(self.observations)

    @property
    def sources(self) -> dict[str, int]:
        return _census(observation.source for observation in self.observations)

    @property
    def outcomes(self) -> dict[str, int]:
        return _census(observation.outcome for observation in self.observations)


@dataclass(frozen=True)
class TuningReport:
    parameters: tuple[ParameterTuning, ...]
    dispatches_read: int
    sources: dict[str, int]
    min_samples: int
    window: int


@dataclass(frozen=True)
class _ParameterSpec:
    key: str
    unit: str
    in_force: float
    prior: float
    basis: str
    overridable: bool = False
    sample: Callable[[Dispatch], float | None] | None = None
    statistic: Callable[[Sequence[float]], float] | None = None


def _is_write(dispatch: Dispatch) -> bool:
    return run_record.is_write_phase(dispatch.entry.get("phase"))


def _positive_number(entry: Mapping[str, object], key: str) -> float | None:

    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _ran(dispatch: Dispatch) -> bool:

    return dispatch.entry.get("outcome") in (run_record.EXECUTED, run_record.FAILED)


def _duration(dispatch: Dispatch) -> float | None:
    if not (_is_write(dispatch) and _ran(dispatch)):
        return None
    return _positive_number(dispatch.entry, "duration_s")


def _context_tokens(dispatch: Dispatch) -> float | None:

    if not _is_write(dispatch):
        return None
    tokens = run_record.positive_int(dispatch.entry, "context_tokens")
    return None if tokens is None else float(tokens)


def _occupancy(dispatch: Dispatch) -> float | None:
    if not _is_write(dispatch):
        return None
    tokens = run_record.positive_int(dispatch.entry, "context_tokens")
    window = run_record.positive_int(dispatch.entry, "context_window")
    if tokens is None or window is None:
        return None
    return tokens / window


def _attempts(dispatch: Dispatch) -> float | None:
    return float(dispatch.attempt) if dispatch.attempt else None


def _build_factor(task_class: str) -> Callable[[Dispatch], float | None]:

    def sample(dispatch: Dispatch) -> float | None:
        if not (_is_write(dispatch) and dispatch.entry.get("task_class") == task_class):
            return None
        working_set = run_record.positive_int(dispatch.entry, "context_tokens")
        scope = run_record.positive_int(dispatch.entry, "scope_tokens")
        if working_set is None or scope is None:
            return None
        return working_set / scope

    return sample


def _quantile(values: Sequence[float], quantile: float) -> float:

    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _ceiling(values: Sequence[float]) -> float:
    return _quantile(values, CEILING_QUANTILE)


def _floor(values: Sequence[float]) -> float:
    return _quantile(values, FLOOR_QUANTILE)


def _backstop(values: Sequence[float]) -> float:
    return max(values) * BACKSTOP_HEADROOM


def _occupancy_backstop(values: Sequence[float]) -> float:
    return min(1.0, _backstop(values))


def _rework_allowance(values: Sequence[float]) -> float:
    return max(0.0, _quantile(values, REWORK_QUANTILE) - 1)


def _median(values: Sequence[float]) -> float:

    return statistics.median(values)


def _declared_default(cls: type, name: str) -> float:

    for declared in fields(cls):
        if declared.name == name:
            return float(declared.default)  # type: ignore[arg-type]
    raise KeyError(f"{cls.__name__} declares no field {name!r}")


_NO_SIGNAL = "no dispatch record carries a signal for this parameter — {}"


def _parameter_specs(
    *,
    runner: config.RunnerConfig,
    policy: config.PolicyConfig,
    sizing: config.SizingConfig,
    worktree: config.WorktreeConfig,
) -> tuple[_ParameterSpec, ...]:

    specs = [
        _ParameterSpec(
            key="runner.runner_timeout",
            unit="s",
            in_force=runner.runner_timeout,
            prior=_declared_default(config.RunnerConfig, "runner_timeout"),
            overridable=True,
            sample=_duration,
            statistic=_backstop,
            basis=(
                f"the longest recorded write dispatch x{BACKSTOP_HEADROOM:g} headroom. A "
                "backstop for the case the spend ceiling and quiet_after cannot see, so "
                "it is set where it never fires in normal operation rather than fitted "
                "to the work distribution"
            ),
        ),
        _ParameterSpec(
            key="runner.stall_after",
            unit="s",
            in_force=runner.stall_after,
            prior=_declared_default(config.RunnerConfig, "stall_after"),
            overridable=True,
            basis=_NO_SIGNAL.format(
                "flagging a dispatch possibly-stuck is not recorded, and neither is the "
                "inter-event gap it would be calibrated against"
            ),
        ),
        _ParameterSpec(
            key="runner.quiet_after",
            unit="s",
            in_force=runner.quiet_after,
            prior=_declared_default(config.RunnerConfig, "quiet_after"),
            overridable=True,
            basis=_NO_SIGNAL.format(
                "a record names the bound that stopped a dispatch but never the silent "
                "gap that triggered it, so the value can only be declared"
            ),
        ),
        _ParameterSpec(
            key="runner.max_agent_processes",
            unit="processes",
            in_force=float(runner.max_agent_processes),
            prior=_declared_default(config.RunnerConfig, "max_agent_processes"),
            overridable=True,
            basis=_NO_SIGNAL.format(
                "how many agent processes were live at once is never written down, and "
                "the bound is API and RAM rather than anything a dispatch reports"
            ),
        ),
        _ParameterSpec(
            key="worktree.concurrency",
            unit="lanes",
            in_force=float(worktree.concurrency),
            prior=float(config.DEFAULT_WORKTREE_CONCURRENCY),
            overridable=True,
            basis=_NO_SIGNAL.format(
                "dispatch_rank records a lane's position within a pass, not how many "
                "lanes were in flight, and throughput against tracker-write and CPU "
                "contention is not metered at all"
            ),
        ),
        _ParameterSpec(
            key="policy.max_rework",
            unit="attempts",
            in_force=float(policy.max_rework),
            prior=float(config.DEFAULT_MAX_REWORK),
            overridable=True,
            sample=_attempts,
            statistic=_rework_allowance,
            basis=(
                f"write dispatches per bead at the {REWORK_QUANTILE:g} quantile, less the "
                "first attempt, which is not rework. Counts every attempt recorded, "
                "including beads that never landed"
            ),
        ),
        _ParameterSpec(
            key="policy.max_subtasks_per_lane",
            unit="beads",
            in_force=float(policy.max_subtasks_per_lane),
            prior=_declared_default(config.PolicyConfig, "max_subtasks_per_lane"),
            overridable=True,
            basis=_NO_SIGNAL.format(
                "a run record is per dispatch and never names the sub-task beads one "
                "lane ran in sequence"
            ),
        ),
        _ParameterSpec(
            key="policy.decider_max_decisions",
            unit="decisions",
            in_force=float(policy.decider_max_decisions),
            prior=_declared_default(config.PolicyConfig, "decider_max_decisions"),
            overridable=True,
            basis=_NO_SIGNAL.format(
                "decide-phase dispatches are recorded per bead, and the bound is per "
                "session — which a run record does not identify"
            ),
        ),
        _ParameterSpec(
            key="policy.sizing.working_set_min",
            unit="tokens",
            in_force=float(sizing.working_set_min),
            prior=float(config.DEFAULT_WORKING_SET_MIN),
            sample=_context_tokens,
            statistic=_floor,
            basis=(
                f"measured working set at the {FLOOR_QUANTILE:g} quantile. The band's "
                "floor refuses a package too small to be worth a lane, so it is read "
                "from the bottom of the distribution lanes actually occupied"
            ),
        ),
        _ParameterSpec(
            key="policy.sizing.working_set_max",
            unit="tokens",
            in_force=float(sizing.working_set_max),
            prior=float(config.DEFAULT_WORKING_SET_MAX),
            sample=_context_tokens,
            statistic=_ceiling,
            basis=(
                f"measured working set at the {CEILING_QUANTILE:g} quantile — the "
                "occupancy a finished lane really reported, not the scope-times-seed "
                "proxy this band was derived from twice before the field existed"
            ),
        ),
        _ParameterSpec(
            key="policy.sizing.context_ceiling",
            unit="fraction",
            in_force=sizing.context_ceiling,
            prior=float(config.DEFAULT_CONTEXT_CEILING),
            sample=_occupancy,
            statistic=_occupancy_backstop,
            basis=(
                f"the fullest window a finished lane really reported x{BACKSTOP_HEADROOM:g} "
                "headroom, clamped to the whole window. Censored evidence: crossing this "
                "ceiling triggers the finalize protocol, so it truncates the very "
                "distribution it is read from — treat the result as a floor on the "
                "ceiling, and check the recorded window before trusting it, because a "
                "declared window is a capability claim that goes stale silently"
            ),
        ),
        _ParameterSpec(
            key="policy.sizing.calibration_min_samples",
            unit="samples",
            in_force=float(sizing.calibration_min_samples),
            prior=float(config.DEFAULT_CALIBRATION_MIN_SAMPLES),
            basis=_NO_SIGNAL.format(
                "how a different minimum would have forecast is not recorded, and this "
                "is the very threshold the report's own seeded/measured split uses"
            ),
        ),
        _ParameterSpec(
            key="policy.sizing.calibration_window",
            unit="samples",
            in_force=float(sizing.calibration_window),
            prior=float(config.DEFAULT_CALIBRATION_WINDOW),
            basis=_NO_SIGNAL.format(
                "nothing records what a wider or narrower window would have produced, "
                "so the window can only be declared"
            ),
        ),
    ]
    specs.extend(
        _ParameterSpec(
            key=f"policy.sizing.build_factor.{task_class}",
            unit="x scope",
            in_force=factor,
            prior=float(config.DEFAULT_BUILD_FACTOR_SEEDS.get(task_class, factor)),
            sample=_build_factor(task_class),
            statistic=_median,
            basis=(
                "median measured working set over declared scope read-cost for this "
                "class. Fitted to context_tokens, never to spend — spend is working set "
                "times a turn count nothing models, which is what basicly-z2wi removed"
            ),
        )
        for task_class, factor in sorted(sizing.build_factors.items())
    )
    return tuple(specs)


def _census(values: Iterable[object]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def render_value(value: float) -> str:

    return str(int(value)) if float(value).is_integer() else f"{value:.4g}"


def _in_force_for(dispatch: Dispatch, spec: _ParameterSpec) -> str:

    if spec.overridable:
        prefix = f"{spec.key}="
        overrides = dispatch.entry.get("config_overrides")
        if isinstance(overrides, (list, tuple)):
            for pair in overrides:
                if isinstance(pair, str) and pair.startswith(prefix):
                    return pair[len(prefix) :]
    return render_value(spec.in_force)


def _cohorts(observations: Sequence[Observation]) -> tuple[ValueCohort, ...]:
    grouped: dict[str, list[Observation]] = {}
    for observation in observations:
        grouped.setdefault(observation.in_force, []).append(observation)
    return tuple(
        ValueCohort(
            in_force=value,
            samples=len(items),
            outcomes=_census(item.outcome for item in items),
            sources=_census(item.source for item in items),
        )
        for value, items in sorted(grouped.items())
    )


def _tune_parameter(
    spec: _ParameterSpec, corpus: Sequence[Dispatch], *, min_samples: int, window: int
) -> ParameterTuning:

    sampler, statistic = spec.sample, spec.statistic
    if sampler is None or statistic is None:
        return _unobserved(spec, min_samples)

    observations = [
        Observation(
            bead=dispatch.bead,
            timestamp=dispatch.timestamp,
            source=dispatch.source,
            outcome=str(dispatch.entry.get("outcome") or "unrecorded"),
            in_force=_in_force_for(dispatch, spec),
            value=value,
        )
        for dispatch, value in ((item, sampler(item)) for item in corpus)
        if value is not None
    ]
    observations = observations[-window:]

    if not observations:
        return _unobserved(spec, min_samples)
    if len(observations) < min_samples:
        status, recommendation = SEEDED, spec.prior
    else:
        status = MEASURED
        recommendation = statistic([item.value for item in observations])

    return ParameterTuning(
        key=spec.key,
        unit=spec.unit,
        in_force=spec.in_force,
        prior=spec.prior,
        cohorts=_cohorts(observations),
        observations=tuple(observations),
        status=status,
        recommendation=recommendation,
        basis=spec.basis,
        min_samples=min_samples,
    )


def _unobserved(spec: _ParameterSpec, min_samples: int) -> ParameterTuning:
    return ParameterTuning(
        key=spec.key,
        unit=spec.unit,
        in_force=spec.in_force,
        prior=spec.prior,
        cohorts=(),
        observations=(),
        status=UNOBSERVED,
        recommendation=None,
        basis=spec.basis,
        min_samples=min_samples,
    )


def tuning_report(repo_root: Path) -> TuningReport:

    sizing = config.load_sizing_config(repo_root)
    specs = _parameter_specs(
        runner=config.load_runner_config(repo_root),
        policy=config.load_policy_config(repo_root),
        sizing=sizing,
        worktree=config.load_worktree_config(repo_root),
    )
    corpus = read_dispatches(repo_root)
    return TuningReport(
        parameters=tuple(
            _tune_parameter(
                spec,
                corpus,
                min_samples=sizing.calibration_min_samples,
                window=sizing.calibration_window,
            )
            for spec in specs
        ),
        dispatches_read=len(corpus),
        sources=_census(dispatch.source for dispatch in corpus),
        min_samples=sizing.calibration_min_samples,
        window=sizing.calibration_window,
    )

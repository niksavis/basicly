"""Advisory tuning report: recorded outcomes against the parameters in force (basicly-3ifz.1).

Almost every number that governs the factory is set by judgment and then never
revisited against what actually happened. The one exception proved the point: the
first measured lane actual corrected a cost estimate that was wrong by ~2.4x. This
module is the readable half of the feedback loop the rest of the parameters have
never had — it pairs the dispatch ledger with the parameter values that were in
force for those dispatches, and says, per parameter, what the evidence supports.

Three properties make it worth trusting, and each is a rule enforced below:

* **Advisory, never self-modifying.** Nothing here writes. A tuning report proposes
  a value and shows the evidence; a human or a gate applies it by editing
  ``basicly.toml``. That is the same split the engine keeps everywhere — deterministic
  checks block, judged checks advise, the engine disposes.
* **A seed never reads as a measurement.** Below ``calibration_min_samples`` the
  recommendation is the declared prior, labelled :data:`SEEDED`, and it names the
  in-force value it would displace. This is the discipline
  :class:`run_record.SpendCalibration` already keeps one layer down.
* **A parameter nothing measures is still listed.** With a sample size of zero and no
  recommendation, plus the basis saying what would have to be recorded for it to have
  one. Omitting it is how ``quiet_after`` came to be declared with no measurement
  behind it and no report that said so — a bound nothing records is a bound nobody
  can tighten.

Both corpora are read and each sample says which it came from (D11). ``.basicly/usage/``
is self-ignored and never leaves the machine that wrote it, while every dispatch also
writes a ``[harness-run]`` marker into the committed tracker export, so a teammate's
clone can produce this report from the tracker alone. A dispatch present in both is one
sample labelled :data:`BOTH`, never two.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass, fields
from pathlib import Path

from . import config, run_record

# --- Which corpus a sample came from (D11) -----------------------------------
# Named per sample rather than per report: the two corpora answer differently on
# purpose, and a reader deciding whether a recommendation travels needs to know
# whether it rests on records only this machine holds.
LOCAL = "local"  # .basicly/usage/run-records.json only — self-ignored, never shared
TRACKER = "tracker"  # a [harness-run] marker in the committed export
BOTH = "both"  # recorded in both; counted once

# --- How a recommendation was reached ----------------------------------------
MEASURED = "measured"  # at least `calibration_min_samples` observations
SEEDED = "seeded"  # some history, but under the minimum: the declared prior stands
UNOBSERVED = "unobserved"  # nothing bearing on this parameter is recorded at all

# Two kinds of number are advised here, and the difference is the asymmetry of being
# wrong about them.
#
# A **band** shapes what gets planned — `working_set_min`/`working_set_max` refuse a
# package as too small or too large — and both refusals are recoverable: merge it with
# a sibling, or split it into more top-level packages. So a band is read at the
# quantiles of what really happened, keeping future packages inside the range lanes
# have actually run in.
CEILING_QUANTILE = 0.9
FLOOR_QUANTILE = 0.1

# A **backstop** — `runner_timeout`, `context_ceiling` — must not fire on healthy work,
# because firing destroys work already in progress. So it is read from the *worst*
# observed run rather than a quantile, with headroom on top so the next slowest run
# does not trip it. Calibrating `runner_timeout` against the work distribution instead
# is exactly what had it killing working lanes (basicly-lpsf).
BACKSTOP_HEADROOM = 2.0

# Attempts one bead may take before the rework allowance is exceeded, read at the
# same ceiling quantile as the bands: `max_rework` counts the attempts *after* the
# first, so the allowance is the attempt count less one.
REWORK_QUANTILE = CEILING_QUANTILE


@dataclass(frozen=True)
class Dispatch:
    """One recorded dispatch, with the corpus it was read from.

    ``entry`` is the raw persisted record rather than a :class:`run_record.RunRecord`:
    this reads history written by older engine versions, where a field may be absent
    or externally tampered, and every extractor below already fails closed on a value
    it cannot use.
    """

    bead: str
    timestamp: str
    source: str
    entry: Mapping[str, object]
    # 1-based position among this bead's write dispatches, chronologically; 0 for a
    # helper dispatch, which is not an attempt at the bead's work.
    attempt: int = 0


def read_dispatches(repo_root: Path) -> tuple[Dispatch, ...]:
    """Every known dispatch, deduplicated across both corpora and source-labelled.

    :func:`run_record.dispatch_history` already unions the two, but it discards which
    side each entry came from — and that is precisely what this report has to state.
    So the union is rebuilt here with the provenance kept, on the same key
    (``bead``, ``timestamp``) and with the same one-sample-per-dispatch rule: counting
    a dispatch twice would double-weight it in every statistic below.

    An entry carrying no usable timestamp cannot be deduplicated and cannot be ordered,
    so it is dropped rather than guessed at — a sample that may be a duplicate is worse
    than a missing one in a report whose whole claim is its sample size.
    """
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
                    # Present in both corpora: one sample, labelled as such. The
                    # tracker's copy is kept because it is the one that travels.
                    seen[key] = Dispatch(found.bead, found.timestamp, BOTH, found.entry)
                    continue
                seen[key] = Dispatch(str(bead_id), stamp, source, entry)
    ordered = sorted(seen.values(), key=lambda item: (item.timestamp, item.bead))
    return tuple(_with_attempts(ordered))


def _with_attempts(ordered: Sequence[Dispatch]) -> list[Dispatch]:
    """Number each bead's write dispatches chronologically, 1-based.

    The observation behind ``[policy] max_rework``: a bead's second write dispatch is
    its first rework. Helper dispatches (a rubric judge, the decider) keep 0 — they are
    dispatches on the same bead and land in the same stream, so counting them would
    read a cheap judge as an extra attempt at the work.
    """
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
    """One dispatch's sample for one parameter, with the value that governed it."""

    bead: str
    timestamp: str
    source: str
    outcome: str
    # The parameter's value in force *for this dispatch* — the session override the
    # record carries, where it carries one, and today's configured value otherwise.
    in_force: str
    value: float


@dataclass(frozen=True)
class ValueCohort:
    """The dispatches recorded under one value of a governed parameter.

    A cohort rather than one flat count because a session override changes what a
    dispatch *is* without changing any committed file (``session.override_pairs``), so
    a corpus can hold dispatches governed by two different values. Pooling them would
    report an outcome distribution under a value that never governed half of it.
    """

    in_force: str
    samples: int
    outcomes: dict[str, int]
    sources: dict[str, int]


@dataclass(frozen=True)
class ParameterTuning:
    """One governed parameter: what governs it now, what was observed, what is advised."""

    # Dotted rather than the ``[section] name`` a TOML file spells it with, because
    # rich reads a leading ``[...]`` in a table cell as a style tag and silently eats
    # it — a report whose first column dropped its section would be worse than ugly.
    key: str
    unit: str
    in_force: float
    # The declared default this engine ships, read from the config loader's own
    # fallback rather than copied. It is what a :data:`SEEDED` recommendation stands
    # on, and it is carried on a measured row too — a reader auditing the seed against
    # the measurement needs both halves (the :class:`run_record.SpendCalibration` rule).
    prior: float
    cohorts: tuple[ValueCohort, ...]
    observations: tuple[Observation, ...]
    status: str
    recommendation: float | None
    basis: str
    min_samples: int

    @property
    def samples(self) -> int:
        """How many observations back this row — the sample size a reader is owed."""
        return len(self.observations)

    @property
    def sources(self) -> dict[str, int]:
        """Sample count per corpus, so the row says where its evidence lives."""
        return _census(observation.source for observation in self.observations)

    @property
    def outcomes(self) -> dict[str, int]:
        """The outcome distribution over every observation, across cohorts."""
        return _census(observation.outcome for observation in self.observations)


@dataclass(frozen=True)
class TuningReport:
    """Every governed parameter, advised from the dispatch ledger. Writes nothing."""

    parameters: tuple[ParameterTuning, ...]
    # The whole corpus this report read, before any parameter filtered it. Named for
    # the reading rather than `dispatches`, which `wired-or-deleted` and `vulture` both
    # match by bare name: a field called `dispatches` here reports a consumer for
    # `run_record.CostRollup.dispatches`, which has none, and retires its genuine
    # suppression (the masking hazard `.scripts/wired_or_deleted.py` names).
    dispatches_read: int
    sources: dict[str, int]
    min_samples: int
    window: int


@dataclass(frozen=True)
class _ParameterSpec:
    """How one governed parameter is read, sampled and advised.

    *sample* and *statistic* are declared together or not at all. Both None is a
    parameter the dispatch ledger records nothing about — a declaration rather than an
    omission: the row still prints with a sample size of zero, and *basis* says what
    would have to be recorded for it to carry a recommendation.
    """

    key: str
    unit: str
    in_force: float
    prior: float
    basis: str
    # True when ``key`` is also a session override key — ``session.set_override`` is
    # per harness *section*, so a top-level ``[runner]``/``[policy]``/``[worktree]``
    # key can be overridden for one run and a nested ``[policy.sizing]`` one cannot.
    # False therefore means the in-force value is always today's configured one.
    overridable: bool = False
    sample: Callable[[Dispatch], float | None] | None = None
    statistic: Callable[[Sequence[float]], float] | None = None


# --- Sample extractors --------------------------------------------------------
#
# Each answers None for a dispatch that is not evidence about its parameter, and the
# rule is the same one every other reader of this ledger keeps: unknown provenance
# fails closed. A helper dispatch is never a lane, an unrecorded phase is not shown to
# be one, and a metered zero is a recording artefact rather than a measurement.


def _is_write(dispatch: Dispatch) -> bool:
    """True for a dispatch recorded as an agent doing a node's build work."""
    return run_record.is_write_phase(dispatch.entry.get("phase"))


def _positive_number(entry: Mapping[str, object], key: str) -> float | None:
    """*entry*'s value at *key* as a positive float, else None.

    The float twin of :func:`run_record.positive_int`, which the int-typed fields below
    use directly. Same rule, and the same reason for rejecting zero: a duration or a
    ratio of exactly zero is a run that never started, and averaging it in would drag
    every statistic here toward a value no dispatch ever produced.
    """
    value = entry.get(key)
    if isinstance(value, bool) or not isinstance(value, (int, float)) or value <= 0:
        return None
    return float(value)


def _ran(dispatch: Dispatch) -> bool:
    """True when an agent process really executed, whatever its exit code.

    A handoff ran nothing and an unstarted dispatch died before its process existed;
    neither is an observation of how long work takes or how much context it holds.
    """
    return dispatch.entry.get("outcome") in (run_record.EXECUTED, run_record.FAILED)


def _duration(dispatch: Dispatch) -> float | None:
    """Wall-clock seconds one write dispatch really ran for."""
    if not (_is_write(dispatch) and _ran(dispatch)):
        return None
    return _positive_number(dispatch.entry, "duration_s")


def _context_tokens(dispatch: Dispatch) -> float | None:
    """The measured working set a write dispatch finished holding.

    The quantity the band is denominated in, and the reason ``context_tokens`` was added
    to the record at all: every working-set number this engine gated on before it was a
    proxy — the tokenized scope times a seed — so the band had only ever been derived by
    re-applying the estimator to its own output.
    """
    if not _is_write(dispatch):
        return None
    tokens = run_record.positive_int(dispatch.entry, "context_tokens")
    return None if tokens is None else float(tokens)


def _occupancy(dispatch: Dispatch) -> float | None:
    """Fraction of the declared window a write dispatch finished occupying."""
    if not _is_write(dispatch):
        return None
    tokens = run_record.positive_int(dispatch.entry, "context_tokens")
    window = run_record.positive_int(dispatch.entry, "context_window")
    if tokens is None or window is None:
        return None
    return tokens / window


def _attempts(dispatch: Dispatch) -> float | None:
    """This dispatch's 1-based attempt number at its bead's work."""
    return float(dispatch.attempt) if dispatch.attempt else None


def _build_factor(task_class: str) -> Callable[[Dispatch], float | None]:
    """Measured working set over declared scope read-cost, for one task class.

    This is the quantity the build factor multiplies, measured directly. It is *not*
    the calibration basicly-z2wi removed: that one fitted a working-set factor to
    **spend**, which is working set times a turn count nothing models, and read as a
    216x error in the factor. ``context_tokens`` is the working set itself, so fitting
    to it is the comparison the factor was always making.
    """

    def sample(dispatch: Dispatch) -> float | None:
        if not (_is_write(dispatch) and dispatch.entry.get("task_class") == task_class):
            return None
        working_set = run_record.positive_int(dispatch.entry, "context_tokens")
        scope = run_record.positive_int(dispatch.entry, "scope_tokens")
        if working_set is None or scope is None:
            return None
        return working_set / scope

    return sample


# --- Statistics ---------------------------------------------------------------


def _quantile(values: Sequence[float], quantile: float) -> float:
    """The observed sample at *quantile*; always a figure some dispatch really produced.

    An observed sample rather than an interpolation, and rounded *up* to the sample
    index so the result sits at or above the quantile asked for. The same rule
    ``decompose`` applies to the unsizeable-lane bound, restated here rather than
    imported because that module sits above this one in the engine's tiers.
    """
    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


def _ceiling(values: Sequence[float]) -> float:
    """The high-water mark a ceiling should sit at."""
    return _quantile(values, CEILING_QUANTILE)


def _floor(values: Sequence[float]) -> float:
    """The low-water mark a floor should sit at."""
    return _quantile(values, FLOOR_QUANTILE)


def _backstop(values: Sequence[float]) -> float:
    """The worst run observed, with :data:`BACKSTOP_HEADROOM` on top."""
    return max(values) * BACKSTOP_HEADROOM


def _occupancy_backstop(values: Sequence[float]) -> float:
    """:func:`_backstop`, clamped to the whole window — a fraction above 1 is not one."""
    return min(1.0, _backstop(values))


def _rework_allowance(values: Sequence[float]) -> float:
    """Attempts at the quantile, less the first — which is not rework."""
    return max(0.0, _quantile(values, REWORK_QUANTILE) - 1)


def _median(values: Sequence[float]) -> float:
    """The central estimate, for a ratio rather than a bound.

    A median rather than a mean for the reason every other statistic over this ledger
    takes one: the measured spread runs orders of magnitude, and one such sample drags
    a mean somewhere no dispatch has ever been.
    """
    return statistics.median(values)


# --- The governed parameters --------------------------------------------------


def _declared_default(cls: type, name: str) -> float:
    """The fallback *cls*'s loader applies when a repo declares nothing for *name*.

    Read from the dataclass rather than copied into a literal here: a prior that is a
    second copy of a number drifts from the one actually in force, and this report's
    whole claim is that it names the value that governed the work.
    """
    for declared in fields(cls):
        if declared.name == name:
            return float(declared.default)  # type: ignore[arg-type]
    raise KeyError(f"{cls.__name__} declares no field {name!r}")


# What must be recorded before a parameter with no ledger signal can be advised. Kept
# as prose on the row rather than dropped, because the honest answer ("nothing measures
# this") looks identical to a silence unless the report says it.
_NO_SIGNAL = "no dispatch record carries a signal for this parameter — {}"


def _parameter_specs(
    *,
    runner: config.RunnerConfig,
    policy: config.PolicyConfig,
    sizing: config.SizingConfig,
    worktree: config.WorktreeConfig,
) -> tuple[_ParameterSpec, ...]:
    """Every governed parameter, in the order the report prints them.

    The set is the factory's own list of numbers set by judgment. A parameter joins it
    whether or not the ledger can advise on one — the ones it cannot are exactly the
    ones nobody has ever been able to tighten.
    """
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


# --- Building the report ------------------------------------------------------


def _census(values: Iterable[object]) -> dict[str, int]:
    """Count *values* into a name -> count map, ordered by name for a stable report."""
    counts: dict[str, int] = {}
    for value in values:
        counts[str(value)] = counts.get(str(value), 0) + 1
    return dict(sorted(counts.items()))


def render_value(value: float) -> str:
    """Render a parameter value the way a reader would type it into ``basicly.toml``.

    Whole numbers print without a decimal point (a concurrency of ``5``, not ``5.0``)
    and everything else to four significant figures, which is more precision than any
    of these parameters is declared with.
    """
    return str(int(value)) if float(value).is_integer() else f"{value:.4g}"


def _in_force_for(dispatch: Dispatch, spec: _ParameterSpec) -> str:
    """The value of *spec* that governed *dispatch*.

    A session override changes what a dispatch is while every committed file stays
    identical, and it is the one per-dispatch record of a parameter's value, so it wins
    over today's configuration where the record carries one. Everything else was run
    under what the config says now — which is an assumption, and the reason the cohort
    is labelled rather than assumed away.
    """
    if spec.overridable:
        prefix = f"{spec.key}="
        overrides = dispatch.entry.get("config_overrides")
        if isinstance(overrides, (list, tuple)):
            for pair in overrides:
                if isinstance(pair, str) and pair.startswith(prefix):
                    return pair[len(prefix) :]
    return render_value(spec.in_force)


def _cohorts(observations: Sequence[Observation]) -> tuple[ValueCohort, ...]:
    """Group *observations* by the value that governed them."""
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
    """Advise one parameter from *corpus*, or say plainly that nothing measures it.

    Three states, and the boundary between them is ``calibration_min_samples``:

    * no observation at all — no recommendation. A number invented from an empty
      sample set is not a recommendation, it is the guess this report exists to replace.
    * some, but under the minimum — the **declared prior**, labelled :data:`SEEDED`.
      Deliberately not the statistic over two samples: below the minimum the history is
      not evidence, and a fitted number carrying a "seeded" label would still be read as
      one. The row names the in-force value the prior would displace.
    * at or above the minimum — the statistic over the newest *window* observations,
      labelled :data:`MEASURED` with its sample size.
    """
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
    # The newest window, taken from the tail — `corpus` is timestamp-ordered, so
    # "recent" means recent. Same bound the spend calibration samples under.
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
    """*spec* with nothing behind it: the value in force, zero samples, no advice."""
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
    """The whole advisory report. Reads the ledger and the config; writes nothing.

    Every path below is a read: the loaders parse ``basicly.toml`` and the local
    overlay, and the ledger readers open the tracker export and the self-ignored usage
    file. No caller of this function may change that — a tuner that edited config would
    be applying its own advice, which is the one thing the factory's advisory/blocking
    split forbids.
    """
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

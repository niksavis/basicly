"""What a working set is predicted to cost, calibrated per model and task class.

What the D8 governor forecasts is a *working set* — the context a lane needs —
and that is not what a run costs. Measured on the basicly-u6jq.1 proof run, the
working-set number under-shot actual context by 2.8-4.8x, and because an agentic
loop re-sends its context every turn, total spend is context times turn count
with nothing in the engine modelling the turn count at all. Spend landed 160-420x
the forecast. Every `forecast` field on the shipped cost rollups is null.

So this turns a working-set estimate into predicted spend with three ratios:
tokens per working-set token (the whole turn multiplier, empirically), USD per
million tokens, and seconds per million tokens. Money and time hang off the token
prediction rather than off the working set, so a single multiplier carries the
loop's behaviour and the other two stay what they are — a price and a rate.

**Keyed per (model, task class), never in aggregate.** The same work costs
different amounts on different models and models are replaced constantly, so a
cross-model average is noise. A record whose model was never recorded cannot join
a key: `model` was null on all 122 historical records, and folding those in would
reintroduce exactly the aggregate this is built to avoid.

Cold start is unavoidable, so each ratio is *seeded from a declared prior* and
replaced by the measured median only once `calibration_min_samples` paired records
exist for that key. The prior travels inside the calibration and each ratio names
its own source, because a seeded number that reads as measured is worse than no
number. And an undeclared ratio with too little history stays None: fail closed on
an indeterminate answer rather than publish a confident zero.

Split out of ``run_record`` when the module-size ratchet caught that module
growing. The boundary is *the price* against *the record*: nothing here reads a
ledger, opens a file or knows what a dispatch record looks like on disk —
``run_record.forecast_errors`` pairs each dispatch's forecast with its actual, and
this module only reduces those pairs to ratios. The samples arrive through
:class:`SampleHistory`, which ``run_record.ForecastErrorReport`` satisfies
structurally, so the module that measures the history need not be imported by the
one that prices it.
"""

from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .dispatch_phase import is_write_phase

# Where one ratio's value came from. Recorded per ratio, not per forecast: history
# accumulates unevenly (copilot bills credits and reports no USD at all), so a
# forecast is routinely measured in tokens and still seeded in money.
PRIOR_RATIO = "prior"
MEASURED_RATIO = "measured"
UNDECLARED_RATIO = "undeclared"


class SpendSample(Protocol):
    """One dispatch whose forecast and actual are both known, as this module reads it.

    Structural rather than imported (``run_record.ForecastError`` is what satisfies it
    today) for the reason the module docstring gives: pricing must not depend on the
    module that reads the ledger. Only the fields a ratio is computed from are
    declared — a wider protocol would make an unrelated field on the recorded type a
    breaking change here.

    Every member is a read-only property rather than a mutable attribute, the rule
    :class:`plan_gate.PlannedFields` records: a plain ``model: str | None`` declares a
    *writable* slot, which the frozen dataclass this describes can never satisfy.
    """

    @property
    def model(self) -> str | None:
        """The model the dispatch ran on, or None when it was never recorded."""
        ...

    @property
    def task_class(self) -> str | None:
        """The class of work the dispatch was sized as."""
        ...

    @property
    def phase(self) -> str | None:
        """The phase it was recorded under (:mod:`basicly.dispatch_phase`)."""
        ...

    @property
    def estimated(self) -> bool:
        """True when the actual is a chars/4 transcript estimate, not reported usage."""
        ...

    @property
    def ratio(self) -> float:
        """Actual tokens over forecast working set — the turn multiplier, empirically."""
        ...

    @property
    def actual_tokens(self) -> int:
        """The tokens the dispatch actually spent; the denominator of price and rate."""
        ...

    @property
    def actual_cost(self) -> float | None:
        """The USD it spent, or None when the adapter metered no money."""
        ...

    @property
    def actual_wall_clock_s(self) -> float | None:
        """The seconds it spent, or None when the adapter metered no duration."""
        ...


class SampleHistory(Protocol):
    """The paired dispatches a calibration may draw on, oldest first.

    Ordering is part of the contract, not an accident of the caller: :func:`spend_samples`
    takes the newest *window* off the tail, so a history that is not chronological would
    silently calibrate from an arbitrary subset.
    """

    @property
    def errors(self) -> Sequence[SpendSample]:
        """Every dispatch that carried both a forecast and a measured actual."""
        ...


@dataclass(frozen=True)
class SpendPrior:
    """The declared seed for the three spend ratios, before any history exists.

    A ratio declared None is deliberately unknown — the forecast then reports no
    number for it rather than inventing one. *basis* is the provenance the numbers
    were derived from, recorded with every forecast so a reader can re-derive them.
    """

    tokens_per_working_set_token: float | None
    usd_per_million_tokens: float | None
    seconds_per_million_tokens: float | None
    basis: str


# The declared prior, derived from the only fully metered packages the tracker
# holds: the three basicly-u6jq.1 lanes (basicly-kjc5.32/.50/.51), whose
# `[harness-cost]` rollups carry the actual tokens, USD and wall clock. Their
# forecast fields are null — that is the hole this closes — so the working-set
# column is the governor's number as recorded in basicly-jr0l.21. Per-lane ratios,
# median taken (a mean of a 162x and a 421x sample lands where no lane has been):
#
#   lane        working set   tokens      mult    USD/Mtok   s/Mtok
#   kjc5.32          57_965    9_430_203  162.7      0.820    109.9
#   kjc5.50          47_847   16_002_352  334.4      0.733     92.2
#   kjc5.51          48_897   20_594_047  421.2      0.714     72.1
#
# Wall clock is summed dispatch duration, so it is agent-busy seconds for the
# package, not calendar time for the lane.
DECLARED_SPEND_PRIOR = SpendPrior(
    tokens_per_working_set_token=334.4,
    usd_per_million_tokens=0.733,
    seconds_per_million_tokens=92.2,
    basis="basicly-u6jq.1 proof run, 3 metered packages, per-lane medians",
)


@dataclass(frozen=True)
class CalibratedRatio:
    """One spend ratio, with where it came from and how much history backs it."""

    value: float | None
    # :data:`PRIOR_RATIO`, :data:`MEASURED_RATIO` or :data:`UNDECLARED_RATIO`.
    source: str
    # Samples that could have measured this ratio — reported even when the prior
    # won, so "9 of 10" is distinguishable from "nothing has ever been metered".
    samples: int = 0


@dataclass(frozen=True)
class SpendCalibration:
    """The ratios one package's spend forecast is computed with, and their provenance."""

    tokens_per_working_set_token: CalibratedRatio
    usd_per_million_tokens: CalibratedRatio
    seconds_per_million_tokens: CalibratedRatio
    # The prior in force, recorded even where it was replaced: it is what a later
    # reader needs to tell a seeded forecast from a measured one, and to audit the
    # seed itself once the measured numbers disagree with it.
    prior: SpendPrior
    model: str | None = None
    task_class: str | None = None
    # Paired records that matched (model, task_class) inside the window.
    pairs: int = 0

    @property
    def measured(self) -> bool:
        """True when history replaced at least one ratio."""
        return any(
            ratio.source == MEASURED_RATIO
            for ratio in (
                self.tokens_per_working_set_token,
                self.usd_per_million_tokens,
                self.seconds_per_million_tokens,
            )
        )


def spend_samples(
    report: SampleHistory, *, model: str | None, task_class: str | None, window: int
) -> list[SpendSample]:
    """The paired records eligible to calibrate spend for (*model*, *task_class*).

    One definition of "eligible", because two readers now need it: :func:`calibrate_spend`
    resolves the ratios from it and the preflight report counts it to say whether a class
    is still on the declared prior. A second copy of the filter is how the two would come
    to disagree about how much history exists — exactly the defect basicly-tcmy.5 fixes
    one layer down, where the bound and the calibration each had their own idea of what a
    lane dispatch was.

    A null *model* or *task_class* matches nothing rather than everything: an unrecorded
    model is unknown provenance, and pooling those samples would rebuild the cross-model
    average this calibration exists to avoid. The newest *window* is taken from the tail,
    which is chronological because :class:`SampleHistory` is ordered oldest-first.
    """
    if not (model and task_class):
        return []
    pairs = [
        error
        for error in report.errors
        if error.model == model
        and error.task_class == task_class
        and not error.estimated
        and is_write_phase(error.phase)
    ]
    return pairs[-window:]


def _calibrated(values: list[float], prior: float | None, minimum: int) -> CalibratedRatio:
    """One ratio: the measured median past *minimum* samples, else the prior, else None.

    A median rather than a mean, for the reason ``ForecastErrorReport.median_ratio``
    gives: the measured spread is 160x to 420x and one sample would drag a mean
    somewhere no dispatch has ever been. An empty sample set never measures, whatever
    *minimum* says — there is no median of nothing.
    """
    if values and len(values) >= minimum:
        return CalibratedRatio(statistics.median(values), MEASURED_RATIO, len(values))
    if prior is None:
        return CalibratedRatio(None, UNDECLARED_RATIO, len(values))
    return CalibratedRatio(prior, PRIOR_RATIO, len(values))


# The calibration bounds arrive as the two ints `[policy.sizing]` declares rather
# than as a SizingConfig: `config` imports `runner`, which imports `run_record`,
# which imports this module, so typing them here would close an import cycle. Same
# stance as build_record — one keyword per input, and no module above pulled downwards.
def calibrate_spend(  # noqa: PLR0913
    report: SampleHistory,
    *,
    model: str | None,
    task_class: str | None,
    min_samples: int,
    window: int,
    prior: SpendPrior = DECLARED_SPEND_PRIOR,
) -> SpendCalibration:
    """Resolve the spend ratios for one (*model*, *task_class*) from *report*.

    The sample set is the paired records — a record carrying both a forecast and a
    measured actual — for exactly this model and class, the newest *window* of them,
    with chars/4-estimated actuals excluded. Below *min_samples* the declared prior
    stands, per ratio.

    **A sample must be a write dispatch** (:data:`dispatch_phase.WRITE_PHASES`). A rubric
    judge and the decider are dispatches on the same bead and land in the same record
    stream, so a filter on model and class alone admits them: the ratio would then be a
    helper's spend over a lane's working set, and a cheap judge would drag the multiplier
    the band and the budget are both computed from. They are excluded here rather than
    left to be excluded incidentally by carrying no forecast — that held only because no
    helper site records sizing today, which is a property of the callers and not of
    this function (basicly-tcmy.5). A record whose phase was never written is excluded
    on the same rule: unknown provenance fails closed.

    This is the *only* place a turn multiplier may be measured. It is legitimate here
    because the quantity being predicted is spend, which is what the samples record.
    The build factor predicts a working set and must never be calibrated the same way
    (basicly-z2wi).

    :func:`spend_samples` owns which records qualify, including why a null *model* or
    *task_class* matches nothing rather than everything.
    """
    pairs = spend_samples(report, model=model, task_class=task_class, window=window)
    costs = [
        error.actual_cost / error.actual_tokens * 1_000_000
        for error in pairs
        if error.actual_cost is not None
    ]
    seconds = [
        error.actual_wall_clock_s / error.actual_tokens * 1_000_000
        for error in pairs
        if error.actual_wall_clock_s is not None
    ]
    return SpendCalibration(
        tokens_per_working_set_token=_calibrated(
            [error.ratio for error in pairs], prior.tokens_per_working_set_token, min_samples
        ),
        usd_per_million_tokens=_calibrated(costs, prior.usd_per_million_tokens, min_samples),
        seconds_per_million_tokens=_calibrated(
            seconds, prior.seconds_per_million_tokens, min_samples
        ),
        prior=prior,
        model=model,
        task_class=task_class,
        pairs=len(pairs),
    )

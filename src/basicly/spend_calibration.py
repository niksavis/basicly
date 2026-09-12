from __future__ import annotations

import statistics
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Protocol

from .dispatch_phase import is_write_phase

PRIOR_RATIO = "prior"
MEASURED_RATIO = "measured"
UNDECLARED_RATIO = "undeclared"


class SpendSample(Protocol):
    @property
    def model(self) -> str | None: ...

    @property
    def task_class(self) -> str | None: ...

    @property
    def phase(self) -> str | None: ...

    @property
    def estimated(self) -> bool: ...

    @property
    def ratio(self) -> float: ...

    @property
    def actual_tokens(self) -> int: ...

    @property
    def actual_cost(self) -> float | None: ...

    @property
    def actual_wall_clock_s(self) -> float | None: ...


class SampleHistory(Protocol):
    @property
    def errors(self) -> Sequence[SpendSample]: ...


@dataclass(frozen=True)
class SpendPrior:
    tokens_per_working_set_token: float | None
    usd_per_million_tokens: float | None
    seconds_per_million_tokens: float | None
    basis: str


DECLARED_SPEND_PRIOR = SpendPrior(
    tokens_per_working_set_token=334.4,
    usd_per_million_tokens=0.733,
    seconds_per_million_tokens=92.2,
    basis="basicly-u6jq.1 proof run, 3 metered packages, per-lane medians",
)


@dataclass(frozen=True)
class CalibratedRatio:
    value: float | None
    source: str
    samples: int = 0


@dataclass(frozen=True)
class SpendCalibration:
    tokens_per_working_set_token: CalibratedRatio
    usd_per_million_tokens: CalibratedRatio
    seconds_per_million_tokens: CalibratedRatio
    prior: SpendPrior
    model: str | None = None
    task_class: str | None = None
    pairs: int = 0

    @property
    def measured(self) -> bool:
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

    if values and len(values) >= minimum:
        return CalibratedRatio(statistics.median(values), MEASURED_RATIO, len(values))
    if prior is None:
        return CalibratedRatio(None, UNDECLARED_RATIO, len(values))
    return CalibratedRatio(prior, PRIOR_RATIO, len(values))


def calibrate_spend(  # noqa: PLR0913
    report: SampleHistory,
    *,
    model: str | None,
    task_class: str | None,
    min_samples: int,
    window: int,
    prior: SpendPrior = DECLARED_SPEND_PRIOR,
) -> SpendCalibration:

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

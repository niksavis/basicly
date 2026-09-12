from __future__ import annotations

from collections import Counter
from pathlib import Path
from typing import TYPE_CHECKING

from . import (
    decompose,
    run_record,
    skill_coverage,
    skill_source,
    tuning,
    ui,
    usage,
)
from .config import load_sizing_config

if TYPE_CHECKING:
    import argparse
    from collections.abc import Sequence


def _spend_accuracy_report(repo_root: Path) -> None:

    accuracy = decompose.spend_accuracy(repo_root, load_sizing_config(repo_root))
    if not accuracy.pairs:
        ui.say(
            "No dispatch can be held to a spend forecast yet, so the band below is "
            "unmeasured rather than met.",
            style="warn",
        )
        return
    median = accuracy.median_ratio
    bases = ", ".join(
        f"{sum(pair.basis == basis for pair in accuracy.pairs)} {basis}"
        for basis in sorted({pair.basis for pair in accuracy.pairs})
    )
    ui.say(
        f"Actual/forecast spend: median {median:.2f}x over {len(accuracy.pairs)} pair(s) "
        f"({bases}), band {1 / decompose.SPEND_RATIO_BAND:.1f}x-"
        f"{decompose.SPEND_RATIO_BAND:.0f}x."
    )
    for violation in accuracy.violations:
        ui.say(violation, style="warn")
    ui.say(
        f"Not held to a spend forecast: {accuracy.unsized} metered write dispatch(es) "
        f"with no forecast at all, {accuracy.unmetered} with no measured actual, "
        f"{accuracy.aborted} the runner reported as failed.",
        style="muted",
    )
    if accuracy.unscoped:
        ui.say(
            "Not comparable, their forecast came from the `assumed:` fallback rather "
            f"than a declared scope: {', '.join(accuracy.unscoped)}.",
            style="muted",
        )
    if accuracy.incomparable:
        ui.say(
            "Not comparable, their recorded working-set forecast is above the band's "
            f"ceiling: {', '.join(accuracy.incomparable)}.",
            style="muted",
        )
    if accuracy.unfinished:
        ui.say(
            "Not scored yet, the ledger still holds them open so their fold is a part "
            f"of the lane: {', '.join(accuracy.unfinished)}.",
            style="muted",
        )


def cmd_forecast(_args: argparse.Namespace) -> int:

    report = run_record.forecast_errors(Path.cwd())
    if report.errors:
        ui.table(
            f"Forecast error per dispatch ({report.paired})",
            ["bead", "class", "model", "forecast", "actual", "ratio", "source"],
            [
                [
                    error.bead,
                    error.task_class or "-",
                    error.model or "-",
                    str(error.forecast_tokens),
                    str(error.actual_tokens) + (" (est)" if error.estimated else ""),
                    f"{error.ratio:.2f}x",
                    error.forecast_source or "-",
                ]
                for error in report.errors
            ],
        )
        median = report.median_ratio
        if median is not None:
            ui.say(f"Median actual/forecast: {median:.2f}x over {report.paired} pair(s).")
            ui.say(
                "The forecast is a working set and the actual is total spend, which an "
                "agentic loop re-sends every turn — so this ratio carries the turn "
                "multiplier as well as any estimator error (basicly-jr0l.21).",
                style="muted",
            )
        for task_class, errors in report.by_task_class().items():
            ratios = sorted(error.ratio for error in errors)
            ui.say(
                f"  {task_class}: {len(errors)} pair(s), {ratios[0]:.2f}x-{ratios[-1]:.2f}x",
                style="muted",
            )
    else:
        ui.say(
            "No dispatch carries both a forecast and a measured actual, so no "
            "forecast error is computable yet.",
            style="warn",
        )
    ui.say(
        f"Unpaired: {report.forecast_only} forecast with no actual, "
        f"{report.actual_only} actual with no forecast, "
        f"{report.unmetered} with neither (handoffs and un-sized helper dispatches).",
        style="muted",
    )
    _spend_accuracy_report(Path.cwd())
    return 0


def _census_text(counts: dict[str, int]) -> str:
    return ", ".join(f"{count} {name}" for name, count in counts.items()) or "-"


def _recommendation_cell(parameter: tuning.ParameterTuning) -> str:

    if parameter.recommendation is None:
        return f"- ({parameter.status})"
    advised = tuning.render_value(parameter.recommendation)
    return f"{advised} ({parameter.status}, n={parameter.samples})"


def _advice_line(parameter: tuning.ParameterTuning) -> str:

    unit = parameter.unit
    if parameter.recommendation is None:
        advice = f"no recommendation ({parameter.status})"
    else:
        values = sorted(item.value for item in parameter.observations)
        advice = (
            f"advised {tuning.render_value(parameter.recommendation)} {unit} from "
            f"{parameter.samples} sample(s) ({parameter.status}), observed "
            f"{tuning.render_value(values[0])}-{tuning.render_value(values[-1])} {unit}"
        )
    return (
        f"  {parameter.key}: {tuning.render_value(parameter.in_force)} {unit} in force, "
        f"{advice} — {parameter.basis}."
    )


def cmd_tuning(_args: argparse.Namespace) -> int:

    report = tuning.tuning_report(Path.cwd())
    ui.table(
        f"Advisory parameter tuning ({report.dispatches_read} dispatch(es) read: "
        f"{_census_text(report.sources)})",
        ["parameter", "in force", "samples", "source", "outcomes", "recommendation"],
        [
            [
                parameter.key,
                f"{tuning.render_value(parameter.in_force)} {parameter.unit}",
                str(parameter.samples),
                _census_text(parameter.sources),
                _census_text(parameter.outcomes),
                _recommendation_cell(parameter),
            ]
            for parameter in report.parameters
        ],
    )
    ui.say(
        f"Measured past {report.min_samples} sample(s) (`[policy.sizing] "
        f"calibration_min_samples`), over the newest {report.window}.",
        style="muted",
    )
    for parameter in report.parameters:
        ui.say(_advice_line(parameter), style="muted")
        for cohort in parameter.cohorts:
            ui.say(
                f"      under {cohort.in_force} {parameter.unit}: {cohort.samples} sample(s), "
                f"{_census_text(cohort.outcomes)} ({_census_text(cohort.sources)})",
                style="muted",
            )
        if parameter.status == tuning.SEEDED:
            ui.say(
                f"      seeded: {parameter.samples} sample(s) is under {parameter.min_samples}, "
                f"so the declared prior {tuning.render_value(parameter.prior)} "
                f"{parameter.unit} stands — it would displace the value in force "
                f"{tuning.render_value(parameter.in_force)} {parameter.unit}.",
                style="warn",
            )
    ui.say(
        "This report changed nothing. A tuner proposes and a human or a gate disposes: "
        "apply a recommendation by editing basicly.toml yourself.",
        style="ok",
    )
    return 0


_UNRESOLVED_ROWS = 15


def cmd_report(_args: argparse.Namespace) -> int:
    repo_root = Path.cwd()
    skills = skill_source.discover_skills(repo_root)
    slugs = [skill.slug for skill in skills]
    commands = usage.catalog_commands(skill.instructions for skill in skills)
    report = usage.build_report(repo_root, slugs, commands)
    if report is None:
        ui.say(
            f"No usage data at {usage.USAGE_FILE} — the tool-usage hook has not "
            "recorded anything in this repo yet.",
            style="warn",
        )
        return 0

    if report.tools:
        ui.table(
            f"Terminal tools ({len(report.tools)})",
            ["tool", "count", "last used"],
            [[e.name, str(e.count), e.last_used] for e in report.tools],
        )
    if report.unresolved:
        shown = report.unresolved[:_UNRESOLVED_ROWS]
        dropped = len(report.unresolved) - len(shown)
        suffix = f", {dropped} lower-count rows not shown" if dropped else ""
        ui.table(
            f"Unresolved heads ({len(report.unresolved)}, "
            f"{sum(e.count for e in report.unresolved)} recorded{suffix}) — no command "
            "of this name resolves here: parser misses, or tools absent from this "
            "checkout. Neither used nor unused",
            ["head", "count", "last used"],
            [[e.name, str(e.count), e.last_used] for e in shown],
        )
    if report.skills:
        ui.table(
            f"Skills ({len(report.skills)})",
            ["skill", "count", "last used"],
            [[e.name, str(e.count), e.last_used] for e in report.skills],
        )
    if report.never_used_skills:
        _say_never_invoked(repo_root, report.never_used_skills)
    else:
        ui.say("Every catalog skill has recorded usage.", style="ok")
    return 0


def _say_never_invoked(repo_root: Path, names: Sequence[str]) -> None:

    split = skill_coverage.partition_never_invoked(repo_root, names)
    ui.say(
        f"Never invoked through the Skill tool ({len(names)}). Not a culling list: the "
        "counter cannot see a body the dispatch brief injects.",
        style="muted",
    )
    for label, group in (
        ("delivered by a dispatch, never self-invoked", split.delivered),
        ("unreachable - no role declares them and no `covers:` block matches", split.unreachable),
    ):
        if group:
            ui.say(f"  {label} ({len(group)}): " + ", ".join(group), style="muted")


UNLABELLED = "unlabelled"


def cmd_outcomes(_args: argparse.Namespace) -> int:

    records = run_record.load_run_records(Path.cwd()) or {}
    counts = Counter(
        entry.get("outcome") or UNLABELLED for runs in records.values() for entry in runs
    )
    total = sum(counts.values())
    if not total:
        ui.say(
            f"No run records at {run_record.RUN_RECORDS_FILE} — no dispatch has been "
            "recorded in this repo yet.",
            style="warn",
        )
        return 0

    ui.table(
        f"Dispatch outcomes ({total} records over {sum(1 for r in records.values() if r)} beads)",
        ["outcome", "count", "share"],
        [[name, str(n), f"{n / total:.0%}"] for name, n in counts.most_common()],
    )
    failed = counts.get(run_record.FAILED, 0)
    ui.say(f"failure rate {failed}/{total} = {failed / total:.1%}")
    ui.say("dispatch outcomes only: no record here says whether a lane reached a result")
    return 0

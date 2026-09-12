from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import math
import re
import statistics
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass, replace
from pathlib import Path

from . import dependency_graph, handoff, plan_gate, plan_record, policy, run_record, runner, tracker
from .config import (
    DEFAULT_BUILD_FACTOR,
    SizingConfig,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)
from .invest import TRIGGER_HEADING, trigger_sentence
from .read_cost import instruction_overhead, scope_read_cost

DEFAULT_CHILD_TYPE = "task"


@dataclass(frozen=True)
class ChildSpec:
    title: str
    acceptance: tuple[str, ...]
    scope: tuple[str, ...]
    type: str = DEFAULT_CHILD_TYPE
    shared: tuple[str, ...] = ()
    depends_on: tuple[str, ...] | None = None
    budget_tokens: int | None = None
    integrity: str | None = None
    demonstration: str | None = None


def parse_children(data: object) -> tuple[ChildSpec, ...]:

    if not isinstance(data, dict):
        raise ValueError(f"plan must be a table with a 'children' list, got {type(data).__name__}")
    raw_children = data.get("children")
    if not (isinstance(raw_children, list) and raw_children):
        raise ValueError("plan needs a non-empty 'children' list")
    children = tuple(_parse_child(entry, index) for index, entry in enumerate(raw_children))
    plan_gate.require_plan(children)
    return children


def _parse_child(entry: object, index: int) -> ChildSpec:
    where = f"children[{index}]"
    if not isinstance(entry, dict):
        raise ValueError(f"{where} must be a table, got {type(entry).__name__}")

    title = entry.get("title")
    if not isinstance(title, str) or not title.strip():
        raise ValueError(f"{where} is missing a non-empty 'title'")

    acceptance = _string_list(entry.get("acceptance"), f"{where} 'acceptance'")
    scope = _string_list(entry.get("scope"), f"{where} 'scope'")

    child_type = entry.get("type", DEFAULT_CHILD_TYPE)
    if not isinstance(child_type, str) or not child_type.strip():
        raise ValueError(f"{where} 'type' must be a non-empty string")

    return ChildSpec(
        title=title.strip(),
        acceptance=acceptance,
        scope=scope,
        type=child_type.strip(),
        shared=_parse_shared(entry.get("shared"), scope, where),
        depends_on=_parse_depends_on(entry.get("depends_on"), where),
        budget_tokens=_parse_budget(entry.get("budget_tokens"), where),
        integrity=_parse_declared_text(entry, where, "integrity"),
        demonstration=_parse_declared_text(entry, where, "demonstration"),
    )


def _parse_depends_on(value: object, where: str) -> tuple[str, ...] | None:

    if value is None:
        return None
    if not isinstance(value, list):
        raise ValueError(f"{where} 'depends_on' must be a list of sibling titles")
    entries: list[str] = []
    for item in value:
        if not (isinstance(item, str) and item.strip()):
            raise ValueError(f"{where} 'depends_on' entries must be non-empty strings")
        entries.append(item.strip())
    return tuple(entries)


def _parse_budget(value: object, where: str) -> int | None:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} 'budget_tokens' must be a whole number of tokens")
    return value


def _parse_declared_text(entry: dict, where: str, field: str) -> str | None:
    value = entry.get(field)
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} {field!r} must be a non-empty string")
    return value.strip()


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and value):
        raise ValueError(f"{where} must be a non-empty list of non-empty strings")
    if not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where} must be a non-empty list of non-empty strings")
    return tuple(v.strip() for v in value)


_WILDCARD_CHARS = "*?["


def _parse_shared(value: object, scope: tuple[str, ...], where: str) -> tuple[str, ...]:

    if value is None:
        return ()
    if not isinstance(value, list):
        raise ValueError(f"{where} 'shared' must be a list of paths already in 'scope'")
    entries: list[str] = []
    for item in value:
        if not (isinstance(item, str) and item.strip()):
            raise ValueError(f"{where} 'shared' entries must be non-empty strings")
        entry = item.strip()
        if any(char in entry for char in _WILDCARD_CHARS):
            raise ValueError(
                f"{where} 'shared' entry {entry!r} is a glob; a shared path must be one literal "
                "path, so a plan cannot exempt a whole subtree from serialization"
            )
        if entry not in scope:
            raise ValueError(
                f"{where} 'shared' entry {entry!r} is not in that child's 'scope'; declare the "
                "path in 'scope' too, so the recorded scope stays the whole truth"
            )
        entries.append(entry)
    return tuple(entries)


def load_plan_text(text: str, fmt: str) -> tuple[ChildSpec, ...]:
    if fmt == "json":
        data = json.loads(text)
    elif fmt == "toml":
        data = tomllib.loads(text)
    else:
        raise ValueError(f"unknown plan format {fmt!r}; expected 'json' or 'toml'")
    return parse_children(data)


def load_plan_file(path: Path) -> tuple[ChildSpec, ...]:
    fmt = "toml" if path.suffix.lower() == ".toml" else "json"
    return load_plan_text(path.read_text(encoding="utf-8"), fmt)


def _segments(glob: str) -> tuple[str, ...]:
    normalized = glob.strip().replace("\\", "/").lstrip("./")
    return tuple(seg for seg in normalized.split("/") if seg)


def _segment_compatible(a: str, b: str) -> bool:
    if a == b or "*" in (a, b):
        return True
    return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)


def _segments_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    if not a and not b:
        return True
    if not a:
        return all(seg == "**" for seg in b)
    if not b:
        return all(seg == "**" for seg in a)
    if a[0] == "**":
        return _segments_overlap(a[1:], b) or _segments_overlap(a, b[1:])
    if b[0] == "**":
        return _segments_overlap(a, b[1:]) or _segments_overlap(a[1:], b)
    return _segment_compatible(a[0], b[0]) and _segments_overlap(a[1:], b[1:])


def globs_overlap(a: str, b: str) -> bool:
    return _segments_overlap(_segments(a), _segments(b))


def scopes_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    return any(globs_overlap(ga, gb) for ga in a for gb in b)


def serializes(
    a: ChildSpec,
    b: ChildSpec,
    *,
    ignoring: str | None = None,
    contended: tuple[str, ...] = (),
) -> bool:

    for path in contended:
        if path != ignoring and not (path in a.shared and path in b.shared):
            return True
    for glob_a in a.scope:
        if glob_a == ignoring:
            continue
        for glob_b in b.scope:
            if glob_b == ignoring or not globs_overlap(glob_a, glob_b):
                continue
            if glob_a in a.shared and glob_b in b.shared:
                continue
            return True
    return False


def _group(
    children: tuple[ChildSpec, ...],
    *,
    owned_only: bool,
    ignoring: str | None = None,
    contended: tuple[str, ...] = (),
) -> tuple[int, ...]:

    specs = tuple(_as_owned(spec) for spec in children) if owned_only else children
    parent = list(range(len(specs)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(specs)):
        for j in range(i + 1, len(specs)):
            if serializes(specs[i], specs[j], ignoring=ignoring, contended=contended):
                parent[find(i)] = find(j)

    labels: dict[int, int] = {}
    groups: list[int] = []
    for i in range(len(children)):
        root = find(i)
        if root not in labels:
            labels[root] = len(labels)
        groups.append(labels[root])
    return tuple(groups)


def _as_owned(spec: ChildSpec) -> ChildSpec:
    return spec if not spec.shared else replace(spec, shared=())


def group_children(
    children: tuple[ChildSpec, ...], contended: tuple[str, ...] = ()
) -> tuple[int, ...]:

    return _group(children, owned_only=False, contended=contended)


def append_only_paths(repo_root: Path) -> tuple[str, ...]:

    return load_worktree_config(repo_root).append_only_paths


def chain_predecessors(groups: tuple[int, ...]) -> tuple[int | None, ...]:

    last_in_group: dict[int, int] = {}
    predecessors: list[int | None] = []
    for index, group in enumerate(groups):
        predecessors.append(last_in_group.get(group))
        last_in_group[group] = index
    return tuple(predecessors)


@dataclass(frozen=True)
class CollapsingPath:
    glob: str
    declarers: tuple[int, ...]
    groups: int
    groups_without: int
    neutralized: bool


def collapsing_paths(
    children: tuple[ChildSpec, ...], contended: tuple[str, ...] = ()
) -> tuple[CollapsingPath, ...]:

    owned = len(set(_group(children, owned_only=True, contended=contended)))
    effective = len(set(_group(children, owned_only=False, contended=contended)))
    found: list[CollapsingPath] = []
    candidates = {glob for child in children for glob in child.scope} | set(contended)
    for glob in sorted(candidates):
        held = () if glob in contended else contended
        without = len(set(_group(children, owned_only=True, ignoring=glob, contended=held)))
        if without <= owned:
            continue
        without_effective = len(
            set(_group(children, owned_only=False, ignoring=glob, contended=held))
        )
        found.append(
            CollapsingPath(
                glob=glob,
                declarers=tuple(i for i, c in enumerate(children) if glob in c.scope),
                groups=owned,
                groups_without=without,
                neutralized=without_effective == effective,
            )
        )
    return tuple(sorted(found, key=lambda item: (-item.groups_without, item.glob)))


def _describe_contended_path(item: CollapsingPath) -> str:
    origin = f"`{item.glob}`: append-only by convention ([worktree] append_only_paths)"
    if item.neutralized:
        return (
            f"{origin}, and every child that appends to it declared it 'shared', so it no longer "
            f"serializes the plan — {item.groups_without} group(s)"
        )
    claim = (
        "and no child declares it"
        if not item.declarers
        else "and not every child that declares it calls it 'shared'"
    )
    return (
        f"{origin} {claim}, so it serializes the plan into {item.groups} group(s) where the "
        f"declared scopes alone support {item.groups_without}. Build them in that order, or give "
        "one child the entry and declare the path in its scope"
    )


def describe_collapsing_path(item: CollapsingPath, contended: tuple[str, ...] = ()) -> str:

    if item.glob in contended:
        return _describe_contended_path(item)
    counts = (
        f"treating every declared path as owned, the plan is {item.groups} group(s) with it "
        f"and {item.groups_without} without"
    )
    if item.neutralized:
        return (
            f"`{item.glob}`: declared shared by the children it would have serialized, so it no "
            f"longer collapses the grouping — {counts}"
        )
    return (
        f"`{item.glob}`: collapses the grouping — {counts}; give it a single owner, or declare it "
        "under 'shared' in every child that only appends its own entry to it"
    )


def collapse_note(collapsing: tuple[CollapsingPath, ...]) -> str:

    live = [item.glob for item in collapsing if not item.neutralized]
    if not live:
        return ""
    return " — collapsing path(s): " + ", ".join(f"`{glob}`" for glob in live)


BUILD_FACTOR_SEED = "seed"
BUILD_FACTOR_CONFIGURED = "configured"


@dataclass(frozen=True)
class CostEstimate:
    scope_tokens: int
    overhead_tokens: int
    build_factor: float
    build_factor_source: str = BUILD_FACTOR_SEED

    @property
    def total(self) -> int:
        return self.overhead_tokens + round(self.scope_tokens * self.build_factor)


def _factor_key(task_class: str, factors: dict[str, float]) -> str:

    return task_class if task_class in factors else DEFAULT_CHILD_TYPE


def build_factor_for(task_class: str, factors: dict[str, float]) -> float:

    return factors.get(_factor_key(task_class, factors), DEFAULT_BUILD_FACTOR)


def build_factor_source(task_class: str, sizing: SizingConfig) -> str:

    key = _factor_key(task_class, sizing.build_factors)
    return BUILD_FACTOR_CONFIGURED if key in sizing.configured_build_factors else BUILD_FACTOR_SEED


def estimate_cost(
    repo_root: Path, spec: ChildSpec, sizing: SizingConfig, overhead: int
) -> CostEstimate:

    return CostEstimate(
        scope_tokens=scope_read_cost(repo_root, spec.scope),
        overhead_tokens=overhead,
        build_factor=build_factor_for(spec.type, sizing.build_factors),
        build_factor_source=build_factor_source(spec.type, sizing),
    )


def parse_scope_section(description: str) -> tuple[str, ...]:

    return plan_record.backticked_entries(description, plan_record.SCOPE_HEADING)


def unparsed_scope_warning(description: str) -> str | None:

    if not plan_record.has_heading(description, plan_record.SCOPE_HEADING):
        return None
    if parse_scope_section(description):
        return None
    return (
        f"the `{plan_record.SCOPE_HEADING}` section parsed to no globs, so every gate "
        "reading it treats this bead as declaring no scope at all — its lane cannot be "
        "sized and the landing scope check is inert. Write each entry as a backticked "
        f"glob on its own line, e.g. {policy.SCOPE_LINE_EXAMPLE}"
    )


WORKING_SET_HEADING = "## Working Set"

WORKING_SET_DECLARED = "declared"
WORKING_SET_FROM_SCOPE = "scope"


def working_set_for(description: str, scope: tuple[str, ...]) -> tuple[tuple[str, ...], str]:
    declared = plan_record.backticked_entries(description, WORKING_SET_HEADING)
    return (declared, WORKING_SET_DECLARED) if declared else (scope, WORKING_SET_FROM_SCOPE)


SCOPE_UNREADABLE = "unreadable"
SCOPE_UNDECLARED = "undeclared"
SCOPE_GREENFIELD = "greenfield"


@dataclass(frozen=True)
class BeadScope:
    task_class: str
    scope: tuple[str, ...]
    working_set: tuple[str, ...]
    working_set_source: str


def _read_class_and_scope(issue: object) -> tuple[BeadScope | None, str]:

    if not isinstance(issue, dict):
        return None, SCOPE_UNREADABLE
    task_class = issue.get("issue_type")
    description = issue.get("description")
    if not (isinstance(task_class, str) and task_class and isinstance(description, str)):
        return None, SCOPE_UNREADABLE
    scope = parse_scope_section(description)
    if not scope:
        return None, SCOPE_UNDECLARED
    return BeadScope(task_class, scope, *working_set_for(description, scope)), ""


def _read_bead(repo_root: Path, bead_id: str) -> tuple[BeadScope | None, str]:
    record = tracker.read_record(repo_root, bead_id)
    if record is None:
        return None, SCOPE_UNREADABLE
    return _read_class_and_scope(record)


def bead_class_and_scope(repo_root: Path, bead_id: str) -> tuple[str, tuple[str, ...]] | None:

    read = _read_bead(repo_root, bead_id)[0]
    return None if read is None else (read.task_class, read.scope)


_SIZING_MARKER = "[harness-sizing]"
_SIZING_HEADER = re.compile(rf"^{re.escape(_SIZING_MARKER)} key=(\S+)$")


def sizing_key_for(task_class: str, globs: Iterable[str]) -> str:

    payload = json.dumps({"type": task_class, "scope": sorted(globs)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def sizing_key(spec: ChildSpec) -> str:
    return sizing_key_for(spec.type, spec.scope)


def _parse_sizing_marker(text: str) -> tuple[str, CostEstimate] | None:
    head, _, payload = text.partition("\n")
    match = _SIZING_HEADER.match(head.strip())
    if match is None:
        return None
    try:
        data = json.loads(payload)
        estimate = CostEstimate(
            scope_tokens=int(data["scope_tokens"]),
            overhead_tokens=int(data["overhead_tokens"]),
            build_factor=float(data["build_factor"]),
            build_factor_source=str(data.get("build_factor_source", BUILD_FACTOR_SEED)),
        )
    except ValueError, TypeError, KeyError:
        return None
    return match.group(1), estimate


def frozen_estimates(repo_root: Path, feature_id: str) -> dict[str, CostEstimate]:

    frozen: dict[str, CostEstimate] = {}
    for row in tracker.try_read_comments(repo_root, feature_id):
        text = str(row.get(tracker.COMMENT_TEXT_KEY, ""))
        parsed = _parse_sizing_marker(text)
        if parsed is not None:
            frozen.setdefault(parsed[0], parsed[1])
    return frozen


def forecast_for(repo_root: Path, task_class: str, globs: tuple[str, ...]) -> CostEstimate | None:

    key = sizing_key_for(task_class, globs)
    for record in tracker.all_records(repo_root):
        for text in tracker.export_comment_texts(record):
            parsed = _parse_sizing_marker(text)
            if parsed is not None and parsed[0] == key:
                return parsed[1]
    return None


FROZEN_FORECAST = "frozen"
DISPATCH_FORECAST = "dispatch"


@dataclass(frozen=True)
class DispatchSizing:
    task_class: str
    estimate: CostEstimate
    source: str
    working_set_source: str = WORKING_SET_FROM_SCOPE

    def record_inputs(self, repo_root: Path) -> dict[str, object]:

        spend: int | None = None
        with contextlib.suppress(RuntimeError, ValueError, OSError):
            spend = dispatch_spend_forecasts(repo_root, (self,), load_sizing_config(repo_root))[
                0
            ].tokens
        return {
            "scope_tokens": self.estimate.scope_tokens,
            "forecast_tokens": self.estimate.total,
            "forecast_spend_tokens": spend,
            "task_class": self.task_class,
            "forecast_source": self.source,
            "build_factor_source": self.estimate.build_factor_source,
        }


@dataclass(frozen=True)
class SizingLookup:
    sizing: DispatchSizing | None
    absence: str = ""


def resolve_dispatch_sizing(repo_root: Path, issue_id: str) -> SizingLookup:

    read, absence = _read_bead(repo_root, issue_id)
    if read is None:
        return SizingLookup(None, absence)
    task_class, globs, source = read.task_class, read.working_set, read.working_set_source
    if globs and scope_read_cost(repo_root, globs) == 0:
        return SizingLookup(None, SCOPE_GREENFIELD)
    frozen = forecast_for(repo_root, task_class, globs)
    if frozen is not None:
        return SizingLookup(DispatchSizing(task_class, frozen, FROZEN_FORECAST, source))
    sizing = load_sizing_config(repo_root)
    estimate = CostEstimate(
        scope_tokens=scope_read_cost(repo_root, globs),
        overhead_tokens=instruction_overhead(repo_root),
        build_factor=build_factor_for(task_class, sizing.build_factors),
        build_factor_source=build_factor_source(task_class, sizing),
    )
    return SizingLookup(DispatchSizing(task_class, estimate, DISPATCH_FORECAST, source))


def dispatch_sizing(repo_root: Path, issue_id: str) -> DispatchSizing | None:

    return resolve_dispatch_sizing(repo_root, issue_id).sizing


def forecast_model(repo_root: Path) -> str | None:

    try:
        config = load_runner_config(repo_root)
        spec = runner.select_runner(config.specs, config.default)
        return runner.resolve_model(spec, repo_root=repo_root).model
    except RuntimeError, ValueError, OSError:
        return None


@dataclass(frozen=True)
class SpendForecast:
    tokens: int | None
    cost: float | None
    wall_clock_s: float | None
    calibration: run_record.SpendCalibration

    @property
    def indeterminate(self) -> bool:
        return self.tokens is None and self.cost is None and self.wall_clock_s is None


def spend_from_working_set(
    working_set: int, calibration: run_record.SpendCalibration
) -> int | None:

    multiplier = calibration.tokens_per_working_set_token.value
    if multiplier is None or working_set <= 0:
        return None
    return round(working_set * multiplier)


def forecast_spend(
    estimate: CostEstimate, calibration: run_record.SpendCalibration
) -> SpendForecast:

    tokens = spend_from_working_set(estimate.total, calibration)
    if tokens is None:
        return SpendForecast(None, None, None, calibration)
    usd = calibration.usd_per_million_tokens.value
    seconds = calibration.seconds_per_million_tokens.value
    return SpendForecast(
        tokens=tokens,
        cost=None if usd is None else tokens / 1_000_000 * usd,
        wall_clock_s=None if seconds is None else tokens / 1_000_000 * seconds,
        calibration=calibration,
    )


def _class_forecasts(
    repo_root: Path,
    pairs: tuple[tuple[str, CostEstimate], ...],
    sizing: SizingConfig,
) -> tuple[SpendForecast, ...]:

    report = run_record.forecast_errors(repo_root)
    model = forecast_model(repo_root)
    return tuple(
        forecast_spend(
            estimate,
            run_record.calibrate_spend(
                report,
                model=model,
                task_class=task_class,
                min_samples=sizing.calibration_min_samples,
                window=sizing.calibration_window,
            ),
        )
        for task_class, estimate in pairs
    )


@dataclass(frozen=True)
class CalibrationStatus:
    model: str | None
    min_samples: int
    samples: dict[str, int]
    build_factor_sources: dict[str, str]

    @property
    def measured_classes(self) -> tuple[str, ...]:

        return tuple(
            name for name, count in sorted(self.samples.items()) if count >= self.min_samples
        )

    @property
    def on_seeds(self) -> bool:
        return not self.measured_classes


def calibration_status(repo_root: Path, sizing: SizingConfig) -> CalibrationStatus:

    report = run_record.forecast_errors(repo_root)
    model = forecast_model(repo_root)
    sampled = {error.task_class for error in report.errors if error.task_class}
    classes = sorted(set(sizing.build_factors) | sampled)
    return CalibrationStatus(
        model=model,
        min_samples=sizing.calibration_min_samples,
        samples={
            name: len(
                run_record.spend_samples(
                    report, model=model, task_class=name, window=sizing.calibration_window
                )
            )
            for name in classes
        },
        build_factor_sources={name: build_factor_source(name, sizing) for name in classes},
    )


def spend_forecasts(
    repo_root: Path,
    children: tuple[ChildSpec, ...],
    estimates: tuple[CostEstimate, ...],
    sizing: SizingConfig,
) -> tuple[SpendForecast, ...]:
    pairs = tuple((spec.type, estimate) for spec, estimate in zip(children, estimates, strict=True))
    return _class_forecasts(repo_root, pairs, sizing)


def dispatch_spend_forecasts(
    repo_root: Path,
    sizings: tuple[DispatchSizing, ...],
    sizing: SizingConfig,
) -> tuple[SpendForecast, ...]:

    return _class_forecasts(
        repo_root, tuple((item.task_class, item.estimate) for item in sizings), sizing
    )


UNSIZED_LANE_TOKENS_SEED = 19_000_000


def unsized_lane_tokens(repo_root: Path, sizing: SizingConfig) -> tuple[int, str]:

    observed: list[tuple[str, int]] = []
    for history in run_record.dispatch_history(repo_root).values():
        for entry in history:
            tokens = entry.get("tokens")
            if (
                entry.get("estimated") is False
                and entry.get("outcome") != run_record.FAILED
                and run_record.is_write_phase(entry.get("phase"))
                and isinstance(tokens, int)
                and tokens > 0
            ):
                observed.append((str(entry.get("timestamp", "")), tokens))
    if not observed:
        return UNSIZED_LANE_TOKENS_SEED, "seed"
    observed.sort()
    window = [tokens for _stamp, tokens in observed[-sizing.calibration_window :]]
    return _quantile_high(window, sizing.unsized_lane_quantile), "measured"


def _quantile_high(values: list[int], quantile: float) -> int:

    if not values:
        raise ValueError("a quantile needs at least one sample")
    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


SPEND_RATIO_BAND = 10.0

RECORDED_SPEND_FORECAST = "recorded"
DERIVED_SPEND_FORECAST = "derived"


@dataclass(frozen=True)
class SpendPair:
    bead: str
    timestamp: str
    forecast_tokens: int
    actual_tokens: int
    basis: str
    task_class: str | None = None
    model: str | None = None
    attempts: int = 1

    @property
    def ratio(self) -> float:
        return self.actual_tokens / self.forecast_tokens

    @property
    def spent(self) -> str:
        over = f" over {self.attempts} dispatches" if self.attempts > 1 else ""
        return f"{self.actual_tokens:,} tokens{over}"

    @property
    def in_band(self) -> bool:
        return 1 / SPEND_RATIO_BAND <= self.ratio <= SPEND_RATIO_BAND


@dataclass(frozen=True)
class SpendAccuracy:
    pairs: tuple[SpendPair, ...] = ()
    unsized: int = 0
    incomparable: tuple[str, ...] = ()
    unmetered: int = 0
    aborted: int = 0
    unscoped: tuple[str, ...] = ()
    unfinished: tuple[str, ...] = ()

    @property
    def median_ratio(self) -> float | None:

        if not self.pairs:
            return None
        return statistics.median(pair.ratio for pair in self.pairs)

    @property
    def violations(self) -> tuple[str, ...]:

        out = sorted(
            (pair for pair in self.pairs if not pair.in_band),
            key=lambda pair: -max(pair.ratio, 1 / pair.ratio),
        )
        return tuple(
            f"{pair.bead} spent {pair.spent} against a {pair.basis} "
            f"forecast of {pair.forecast_tokens:,} ({pair.ratio:.3f}x), outside the "
            f"{SPEND_RATIO_BAND:.0f}x band: a grant sized from that forecast is wrong "
            f"by the same factor"
            for pair in out
        )


def _fold_lane(attempts: tuple[SpendPair, ...]) -> SpendPair:

    last = attempts[-1]
    return replace(
        last,
        actual_tokens=sum(attempt.actual_tokens for attempt in attempts),
        attempts=len(attempts),
    )


_CLOSED_STATUS = "closed"


def _unfinished_beads(repo_root: Path, lane_folds: list[SpendPair]) -> tuple[str, ...]:

    status = {
        str(record.get("id")): str(record.get("status"))
        for record in tracker.all_records(repo_root)
    }
    held = {
        fold.bead for fold in lane_folds if status.get(fold.bead, _CLOSED_STATUS) != _CLOSED_STATUS
    }
    return tuple(sorted(held))


def spend_accuracy(repo_root: Path, sizing: SizingConfig) -> SpendAccuracy:

    report = run_record.forecast_errors(repo_root)
    pairs: list[SpendPair] = []
    incomparable: list[str] = []
    unscoped: list[str] = []
    unsized = unmetered = aborted = 0
    for bead_id, history in sorted(run_record.dispatch_history(repo_root).items()):
        for entry in history:
            if not isinstance(entry, dict) or not run_record.is_write_phase(entry.get("phase")):
                continue
            actual = run_record.positive_int(entry, "tokens")
            if actual is None or entry.get("estimated") is not False:
                unmetered += 1
                continue
            if entry.get("outcome") == run_record.FAILED:
                aborted += 1
                continue
            source = entry.get("forecast_source")
            if isinstance(source, str) and source.startswith("assumed:"):
                unscoped.append(bead_id)
                continue
            if entry.get("scope_tokens") == 0:
                unscoped.append(bead_id)
                continue
            task_class = entry.get("task_class")
            model = entry.get("model")
            recorded = run_record.positive_int(entry, "forecast_spend_tokens")
            forecast, basis = recorded, RECORDED_SPEND_FORECAST
            if forecast is None:
                working_set = run_record.positive_int(entry, "forecast_tokens")
                if working_set is None:
                    unsized += 1
                    continue
                if working_set > sizing.working_set_max:
                    incomparable.append(bead_id)
                    continue
                forecast, basis = (
                    spend_from_working_set(
                        working_set,
                        run_record.calibrate_spend(
                            report,
                            model=model if isinstance(model, str) else None,
                            task_class=task_class if isinstance(task_class, str) else None,
                            min_samples=sizing.calibration_min_samples,
                            window=sizing.calibration_window,
                        ),
                    ),
                    DERIVED_SPEND_FORECAST,
                )
                if forecast is None:
                    unsized += 1
                    continue
            pairs.append(
                SpendPair(
                    bead=bead_id,
                    timestamp=str(entry.get("timestamp", "")),
                    forecast_tokens=forecast,
                    actual_tokens=actual,
                    basis=basis,
                    task_class=task_class if isinstance(task_class, str) else None,
                    model=model if isinstance(model, str) else None,
                )
            )
    lanes: dict[str, list[SpendPair]] = {}
    for pair in sorted(pairs, key=lambda pair: (pair.timestamp, pair.bead)):
        lanes.setdefault(pair.bead, []).append(pair)
    lane_folds = sorted(
        (_fold_lane(tuple(attempts)) for attempts in lanes.values()),
        key=lambda pair: (pair.timestamp, pair.bead),
    )
    unfinished = _unfinished_beads(repo_root, lane_folds)
    return SpendAccuracy(
        pairs=tuple(pair for pair in lane_folds if pair.bead not in frozenset(unfinished)),
        unsized=unsized,
        incomparable=tuple(sorted(set(incomparable))),
        unmetered=unmetered,
        aborted=aborted,
        unscoped=tuple(sorted(set(unscoped))),
        unfinished=unfinished,
    )


def freeze_estimate(
    repo_root: Path,
    feature_id: str,
    key: str,
    estimate: CostEstimate,
    spend: SpendForecast | None = None,
) -> None:

    payload = json.dumps(
        {
            "scope_tokens": estimate.scope_tokens,
            "overhead_tokens": estimate.overhead_tokens,
            "build_factor": estimate.build_factor,
            "build_factor_source": estimate.build_factor_source,
            "total": estimate.total,
            "spend": None if spend is None else asdict(spend),
        },
        sort_keys=True,
    )
    with contextlib.suppress(RuntimeError, OSError):
        tracker.try_add_comment(repo_root, feature_id, f"{_SIZING_MARKER} key={key}\n{payload}")


@dataclass(frozen=True)
class PlanVerdict:
    estimates: tuple[CostEstimate, ...]
    violations: tuple[str, ...]
    keys: tuple[str, ...]
    frozen: dict[str, CostEstimate]
    spend: tuple[SpendForecast, ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.violations)


def estimate_plan(
    repo_root: Path, children: tuple[ChildSpec, ...], *, feature_id: str | None = None
) -> PlanVerdict:

    sizing = load_sizing_config(repo_root)
    frozen = frozen_estimates(repo_root, feature_id) if feature_id is not None else {}
    overhead = instruction_overhead(repo_root)
    keys = tuple(sizing_key(spec) for spec in children)
    estimates = tuple(
        frozen.get(key) or estimate_cost(repo_root, spec, sizing, overhead)
        for spec, key in zip(children, keys, strict=True)
    )
    violations = tuple(
        message
        for spec, estimate in zip(children, estimates, strict=True)
        if (
            message := policy.check_working_set(
                spec.title, estimate.total, estimate.scope_tokens, sizing
            )
        )
    )
    return PlanVerdict(
        estimates=estimates,
        violations=violations,
        keys=keys,
        frozen=frozen,
        spend=spend_forecasts(repo_root, children, estimates, sizing),
    )


def govern_working_set(
    repo_root: Path, children: tuple[ChildSpec, ...], *, feature_id: str | None = None
) -> tuple[CostEstimate, ...]:

    verdict = estimate_plan(repo_root, children, feature_id=feature_id)
    if verdict.refused:
        raise ValueError(
            "sizing governor refused the decomposition:\n" + "\n".join(verdict.violations)
        )
    if feature_id is not None:
        for key, estimate, spend in zip(
            verdict.keys, verdict.estimates, verdict.spend, strict=True
        ):
            if key not in verdict.frozen:
                freeze_estimate(repo_root, feature_id, key, estimate, spend)
    return verdict.estimates


def _child_body(spec: ChildSpec, trigger: str = "") -> str:

    return policy.compose_body(
        spec.type,
        {
            TRIGGER_HEADING: trigger,
            "## Acceptance Criteria": "\n".join(f"- {item}" for item in spec.acceptance),
            plan_record.SCOPE_HEADING: "\n".join(f"- `{glob}`" for glob in spec.scope),
            plan_record.PLAN_HEADING: plan_record.render_plan_section(
                spec.depends_on or (),
                spec.budget_tokens or 0,
                spec.integrity or "",
                spec.demonstration or "",
            ),
        },
    )


@dataclass(frozen=True)
class CreatedChild:
    issue_id: str
    spec: ChildSpec
    group: int
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class DecomposeResult:
    feature_id: str
    children: tuple[CreatedChild, ...]
    groups: tuple[tuple[str, ...], ...]
    collapsing: tuple[CollapsingPath, ...] = ()

    @property
    def serial_order(self) -> tuple[str, ...]:
        return tuple(child.issue_id for child in self.children)

    @property
    def parallel_groups(self) -> int:
        return len(self.groups)


def feature_labels(record: dict) -> tuple[str, ...]:

    raw = record.get("labels") or []
    return tuple(str(label) for label in raw if str(label).strip())


def _create_child(
    repo_root: Path,
    feature_id: str,
    spec: ChildSpec,
    labels: tuple[str, ...] = (),
    trigger: str = "",
) -> str:
    args = ["create", spec.title, "-t", spec.type, "--parent", feature_id]
    if labels:
        args += ["-l", ",".join(labels)]
    args += ["-d", _child_body(spec, trigger), "--json"]
    return tracker.create_record(repo_root, args)


def _assert_no_new_cycles(repo_root: Path, created_ids: set[str]) -> None:

    for cycle in dependency_graph.blocking_cycles(repo_root):
        members = set(cycle)
        if members & created_ids:
            raise RuntimeError(f"decomposition introduced a dependency cycle: {sorted(members)}")


def decompose(repo_root: Path, feature_id: str, children: tuple[ChildSpec, ...]) -> DecomposeResult:

    if not children:
        raise ValueError("decompose needs at least one child spec")

    plan_gate.require_plan(children)
    govern_working_set(repo_root, children, feature_id=feature_id)
    contended = append_only_paths(repo_root)
    groups = group_children(children, contended)
    predecessors = chain_predecessors(groups)

    parent = tracker.read_record(repo_root, feature_id) or {}
    inherited = feature_labels(parent)
    trigger = trigger_sentence(str(parent.get("description") or ""))
    issue_ids = [
        _create_child(repo_root, feature_id, spec, inherited, trigger) for spec in children
    ]
    by_title = {spec.title: issue_ids[index] for index, spec in enumerate(children)}

    created: list[CreatedChild] = []
    for index, spec in enumerate(children):
        pred = predecessors[index]
        wanted = [by_title[dep] for dep in (spec.depends_on or ())]
        if pred is not None:
            wanted.append(issue_ids[pred])
        depends_on = tuple(dict.fromkeys(wanted))
        for dep_id in depends_on:
            tracker.write(repo_root, ["dep", "add", issue_ids[index], dep_id, "-t", "blocks"])
        created.append(CreatedChild(issue_ids[index], spec, groups[index], depends_on))

    _assert_no_new_cycles(repo_root, set(issue_ids))

    grouped: dict[int, list[str]] = {}
    for child in created:
        grouped.setdefault(child.group, []).append(child.issue_id)
    group_tuples = tuple(tuple(grouped[g]) for g in sorted(grouped))

    result = DecomposeResult(
        feature_id, tuple(created), group_tuples, collapsing_paths(children, contended)
    )
    handoff.record(repo_root, feature_id, handoff.IMPLEMENTATION_PLAN, handoff.plan_payload(result))
    return result


@dataclass(frozen=True)
class PlannedChild:
    spec: ChildSpec
    group: int
    predecessor: int | None


def preview(
    children: tuple[ChildSpec, ...], contended: tuple[str, ...] = ()
) -> tuple[PlannedChild, ...]:

    groups = group_children(children, contended)
    predecessors = chain_predecessors(groups)
    return tuple(
        PlannedChild(spec, groups[index], predecessors[index])
        for index, spec in enumerate(children)
    )

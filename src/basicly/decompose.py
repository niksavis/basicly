"""Decomposer: turn a feature into child ``br`` issues + a dependency graph.

Thin engine, deterministic core: the *agent* proposes the decomposition (child
titles, per-child acceptance criteria, and declared file-scope globs); this
module VALIDATES the proposal, RECORDS it in ``br`` (child issues under the
feature, with acceptance criteria so ``br lint`` DoR passes), and computes
parallel-safety **deterministically** from the declared scopes — it never
AI-guesses which tracks are safe to build concurrently.

Parallel-safety is encoded in the dependency graph, not a side flag: children
whose declared file-scopes are pairwise disjoint land in separate *groups* that
carry no sibling ``blocks`` deps (safe to build in parallel worktrees); any
scope overlap unions the involved children into one group that is serialized in
declared order via a ``blocks`` chain. The absence of a sibling ``blocks`` edge
*is* the parallel-safe signal, so the loop/merge-queue (onb.5/onb.6) derive
concurrency straight from ``br dep tree`` — ``br`` stays the single source of
truth.

Scope overlap is a pure glob-intersection over the declared patterns (no
filesystem lookup), so a child that will *create* a not-yet-existing file is
still compared correctly and the result is fully reproducible.
"""

from __future__ import annotations

import contextlib
import fnmatch
import hashlib
import json
import re
import statistics
import tomllib
from collections.abc import Iterable
from dataclasses import asdict, dataclass
from pathlib import Path

from . import br, policy, run_record, runner
from .br import run_br as _run_br
from .config import (
    DEFAULT_BUILD_FACTOR,
    SizingConfig,
    load_runner_config,
    load_sizing_config,
)

DEFAULT_CHILD_TYPE = "task"


# --- Plan model & parsing ---------------------------------------------------


@dataclass(frozen=True)
class ChildSpec:
    """One agent-proposed child track: a title, acceptance criteria, and file scope."""

    title: str
    acceptance: tuple[str, ...]
    scope: tuple[str, ...]
    type: str = DEFAULT_CHILD_TYPE


def parse_children(data: object) -> tuple[ChildSpec, ...]:
    """Validate a parsed plan document into child specs.

    Expects ``{"children": [ {title, acceptance, scope, type?}, ... ]}``. Raises
    ``ValueError`` on any malformed entry rather than silently dropping a track —
    a lost child would be built by nobody. A child must declare a non-empty scope
    so parallel-safety is computable; refusing to guess is the whole point.
    """
    if not isinstance(data, dict):
        raise ValueError(f"plan must be a table with a 'children' list, got {type(data).__name__}")
    raw_children = data.get("children")
    if not (isinstance(raw_children, list) and raw_children):
        raise ValueError("plan needs a non-empty 'children' list")
    return tuple(_parse_child(entry, index) for index, entry in enumerate(raw_children))


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
    )


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and value):
        raise ValueError(f"{where} must be a non-empty list of non-empty strings")
    if not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where} must be a non-empty list of non-empty strings")
    return tuple(v.strip() for v in value)


def load_plan_text(text: str, fmt: str) -> tuple[ChildSpec, ...]:
    """Parse plan *text* in ``json`` or ``toml`` format into child specs."""
    if fmt == "json":
        data = json.loads(text)
    elif fmt == "toml":
        data = tomllib.loads(text)
    else:
        raise ValueError(f"unknown plan format {fmt!r}; expected 'json' or 'toml'")
    return parse_children(data)


def load_plan_file(path: Path) -> tuple[ChildSpec, ...]:
    """Parse a plan file, choosing the format from its suffix (``.toml`` else JSON)."""
    fmt = "toml" if path.suffix.lower() == ".toml" else "json"
    return load_plan_text(path.read_text(encoding="utf-8"), fmt)


# --- Deterministic scope overlap & grouping ---------------------------------


def _segments(glob: str) -> tuple[str, ...]:
    normalized = glob.strip().replace("\\", "/").lstrip("./")
    return tuple(seg for seg in normalized.split("/") if seg)


def _segment_compatible(a: str, b: str) -> bool:
    """True when two single path segments can match a common name."""
    if a == b or "*" in (a, b):
        return True
    return fnmatch.fnmatch(a, b) or fnmatch.fnmatch(b, a)


def _segments_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True when two segment lists can match a common path (``**`` spans segments)."""
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
    """True when glob patterns *a* and *b* can match a common path."""
    return _segments_overlap(_segments(a), _segments(b))


def scopes_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """True when any glob in scope *a* can match a common path with any glob in *b*."""
    return any(globs_overlap(ga, gb) for ga in a for gb in b)


def group_children(children: tuple[ChildSpec, ...]) -> tuple[int, ...]:
    """Assign each child a group index; overlapping scopes share a group.

    Union-find over pairwise scope overlap: the transitive closure of overlap is
    one group (serialized), while children with no overlap to any group member
    stay separate (parallel-safe). Group indices are assigned by first-seen child
    so the numbering is deterministic and stable.
    """
    parent = list(range(len(children)))

    def find(i: int) -> int:
        while parent[i] != i:
            parent[i] = parent[parent[i]]
            i = parent[i]
        return i

    for i in range(len(children)):
        for j in range(i + 1, len(children)):
            if scopes_overlap(children[i].scope, children[j].scope):
                parent[find(i)] = find(j)

    labels: dict[int, int] = {}
    groups: list[int] = []
    for i in range(len(children)):
        root = find(i)
        if root not in labels:
            labels[root] = len(labels)
        groups.append(labels[root])
    return tuple(groups)


def chain_predecessors(groups: tuple[int, ...]) -> tuple[int | None, ...]:
    """Index of each child's immediate predecessor within its group (else None).

    Chaining consecutive same-group members in declared order yields one linear
    ``blocks`` chain per group — a fixed serial order — with no chain across
    groups, so distinct groups stay parallel-safe.
    """
    last_in_group: dict[int, int] = {}
    predecessors: list[int | None] = []
    for index, group in enumerate(groups):
        predecessors.append(last_in_group.get(group))
        last_in_group[group] = index
    return tuple(predecessors)


# --- Context-cost sizing estimator (basicly-kjc5.2, factory design D8) -------

# The projected agent-neutral instruction file every dispatch prompt points at;
# its size is context every lane pays before reading any scope material.
INSTRUCTIONS_FILE = "AGENTS.md"

# One scope-glob line as _child_body records it under "## Scope".
_SCOPE_LINE = re.compile(r"^- `([^`]+)`$")


def _text_tokens(text: str) -> int:
    """Deterministic chars/4 token estimate (design 7.5: no tokenizer dependency)."""
    return len(text) // 4


def instruction_overhead(repo_root: Path) -> int:
    """Fixed per-repo instruction overhead: the projected AGENTS.md, tokenized.

    Computed by tokenizing the projected instructions, never configured
    (design section 6). A repo without the file contributes zero; non-UTF-8
    content still counts by size via replacement (same stance as scope files).
    """
    try:
        path = repo_root / INSTRUCTIONS_FILE
        return _text_tokens(path.read_text(encoding="utf-8", errors="replace"))
    except OSError:
        return 0


def _scope_files(repo_root: Path, scope: tuple[str, ...]) -> set[Path]:
    """The existing files matching any of the declared scope globs.

    Only a literal ``./`` prefix is stripped — a bare ``lstrip`` would eat the
    leading dot of a dot-directory scope (``.claude/**``) and silently zero its
    read-cost. A leading ``/`` is relativized; a pattern the glob engine still
    rejects (e.g. drive-anchored on Windows) is skipped, never fatal — the
    governor treats it as unreadable material, matching the scope_read_cost
    stance.
    """
    files: set[Path] = set()
    for pattern in scope:
        normalized = pattern.strip().replace("\\", "/")
        while normalized.startswith("./"):
            normalized = normalized[2:]
        normalized = normalized.lstrip("/")
        if not normalized:
            continue
        try:
            matches = list(repo_root.glob(normalized))
        except ValueError, NotImplementedError, OSError:
            continue
        for path in matches:
            if path.is_file():
                files.add(path)
    return files


def scope_read_cost(repo_root: Path, scope: tuple[str, ...]) -> int:
    """Tokenized size of the existing files matching the declared scope globs.

    A glob matching nothing — a file the child will create — contributes zero:
    there is nothing to read yet. Unreadable files are skipped (telemetry-grade
    input, never fatal); binary content still counts by size via replacement.
    """
    total = 0
    for path in _scope_files(repo_root, scope):
        try:
            total += _text_tokens(path.read_text(encoding="utf-8", errors="replace"))
        except OSError:
            continue
    return total


@dataclass(frozen=True)
class CostEstimate:
    """One child's deterministic context-cost estimate (D8: estimate at decompose)."""

    scope_tokens: int
    overhead_tokens: int
    build_factor: float

    @property
    def total(self) -> int:
        """Estimated working-set tokens: overhead + scope read-cost x build factor."""
        return self.overhead_tokens + round(self.scope_tokens * self.build_factor)


def build_factor_for(task_class: str, factors: dict[str, float]) -> float:
    """The build factor for *task_class*.

    An unlisted task class uses the ``task`` factor (the most conservative seed),
    falling back to :data:`DEFAULT_BUILD_FACTOR` when even that is absent. Shared by
    :func:`estimate_cost` and :func:`dispatch_sizing` so a plan's estimate and the
    same package's dispatch-time forecast cannot be computed two different ways.
    """
    return factors.get(task_class, factors.get(DEFAULT_CHILD_TYPE, DEFAULT_BUILD_FACTOR))


def estimate_cost(
    repo_root: Path, spec: ChildSpec, factors: dict[str, float], overhead: int
) -> CostEstimate:
    """Estimate *spec*'s working-set cost from its declared scope and task class."""
    return CostEstimate(
        scope_tokens=scope_read_cost(repo_root, spec.scope),
        overhead_tokens=overhead,
        build_factor=build_factor_for(spec.type, factors),
    )


def parse_scope_section(description: str) -> tuple[str, ...]:
    """The scope globs recorded under a ``## Scope`` heading, as _child_body writes them."""
    scope: list[str] = []
    in_scope = False
    for line in description.splitlines():
        stripped = line.strip()
        if stripped.startswith("## "):
            in_scope = stripped == "## Scope"
            continue
        if in_scope:
            match = _SCOPE_LINE.match(stripped)
            if match:
                scope.append(match.group(1))
    return tuple(scope)


def _class_and_scope_of(issue: object) -> tuple[str, tuple[str, ...]] | None:
    """The task class and declared scope carried by one issue record, if both are there."""
    if not isinstance(issue, dict):
        return None
    task_class = issue.get("issue_type")
    description = issue.get("description")
    if not (isinstance(task_class, str) and task_class and isinstance(description, str)):
        return None
    scope = parse_scope_section(description)
    return (task_class, scope) if scope else None


def bead_class_and_scope(repo_root: Path, bead_id: str) -> tuple[str, tuple[str, ...]] | None:
    """The task class and declared scope of *bead_id*, or None when unreadable."""
    try:
        proc = _run_br(repo_root, ["show", bead_id, "--json"])
        data = json.loads(proc.stdout)
    except RuntimeError, ValueError, OSError:
        return None
    return _class_and_scope_of(data[0] if isinstance(data, list) and data else data)


def calibrated_build_factors(repo_root: Path, sizing: SizingConfig) -> dict[str, float]:
    """Build factors per task class: measured from run-record telemetry, else seeds.

    A calibration sample is one executed run with adapter-reported tokens
    (chars/4-estimated samples are excluded — design 7.5's down-weighting at its
    simplest) on a bead whose task class and declared scope are readable from the
    tracker (decompose-created children record scope under ``## Scope``). Per
    class, the most recent ``calibration_window`` samples yield
    ``factor = reported tokens / scope read-cost``, and the median overrides the
    seed only past ``calibration_min_samples``. Best-effort by construction:
    an unreadable bead or malformed record is skipped, never fatal — with few
    samples the seeds stand.

    Scope read-cost comes from the record's own ``scope_tokens``, persisted by the
    dispatch that produced the sample (basicly-kjc5.30), so a sample means the same
    thing whenever it is read. Only a record written before that — which has no
    ``scope_tokens`` — falls back to measuring the current tree.

    Samples come from the tracker as well as from local telemetry
    (basicly-kjc5.50): ``.basicly/usage/`` is self-ignored, so reading it alone
    meant a fresh clone calibrated from the seeds and two machines sized the same
    plan differently. Every dispatch already writes a ``[harness-run]`` marker and
    the export carries comments, so the shared ledger answers — and answers with
    no br invocation, since the exported record also carries the task class and the
    ``## Scope`` section each sample needs.
    """
    factors = dict(sizing.build_factors)
    exported = {str(record["id"]): record for record in br.export_records(repo_root)}
    records = run_record.dispatch_history(repo_root)
    if not records:
        return factors

    samples: dict[str, list[tuple[str, float]]] = {}
    for bead_id, history in records.items():
        reported = [
            entry
            for entry in history
            if entry.get("estimated") is False
            and isinstance(entry.get("tokens"), int)
            and entry["tokens"] > 0
        ]
        if not reported:
            continue
        # The exported record answers for free; only a bead the export does not
        # carry (recorded locally but not yet flushed) costs a br read.
        info = _class_and_scope_of(exported.get(bead_id)) or bead_class_and_scope(
            repo_root, bead_id
        )
        if info is None:
            continue
        task_class, scope = info
        # Fallback only: a record written before the dispatch persisted its own
        # scope cost has to be measured against the tree as it stands now, which
        # is the drift basicly-kjc5.30 removes going forward.
        measured_now = scope_read_cost(repo_root, scope)
        for entry in reported:
            # The scope cost as it was at dispatch, when that dispatch recorded it
            # — so a sample keeps meaning what it meant, however the files have
            # grown since.
            recorded = entry.get("scope_tokens")
            cost = recorded if isinstance(recorded, int) and recorded > 0 else measured_now
            if cost <= 0:
                continue
            timestamp = str(entry.get("timestamp", ""))
            samples.setdefault(task_class, []).append((timestamp, entry["tokens"] / cost))

    for task_class, class_samples in samples.items():
        recent = sorted(class_samples)[-sizing.calibration_window :]
        if len(recent) >= sizing.calibration_min_samples:
            factors[task_class] = statistics.median(factor for _, factor in recent)
    return factors


# --- Frozen estimates (basicly-kjc5.30, design D9) ---------------------------
#
# D8 calls the sizing estimate deterministic, and it is — per invocation. Across
# invocations it drifts twice over: `calibrated_build_factors` takes a rolling
# median of the most recent samples, and scope read-cost is measured against the
# tree as it stands. So `govern_working_set` can refuse today the very plan it
# accepted last week, with neither the plan nor the code touched. D9 forbids a
# gate whose verdict depends on when it ran.
#
# So the verdict is frozen with the plan that earned it: when the governor accepts
# a decomposition it records each child's estimate, keyed by the content the
# estimate is a function of (task class + declared scope). Governing the same plan
# again reuses those numbers rather than recomputing them, so the answer is stable.
#
# Recorded on the *feature*, not the child, because no child exists yet — the
# governor runs before anything is created, precisely so a refused plan creates
# nothing. The feature bead is the only carrier in existence at the moment the
# verdict is made.
#
# Evidence, not state (D11): nothing branches on it beyond this reuse, so a
# missing or malformed marker degrades to recomputing rather than failing.

_SIZING_MARKER = "[harness-sizing]"
_SIZING_HEADER = re.compile(rf"^{re.escape(_SIZING_MARKER)} key=(\S+)$")


def sizing_key_for(task_class: str, scope: Iterable[str]) -> str:
    """Content key for the estimate of a task class over a declared scope.

    Derived from exactly what the estimate is a function of — the task class picks
    the build factor and the declared scope sets the read cost. Deliberately not
    the title: retitling a child must not silently re-open a settled verdict.

    Takes the content rather than a :class:`ChildSpec` so a *shipped bead* can key
    into the same freeze (basicly-kjc5.50): by then the spec is long gone, but the
    bead still carries its class and its ``## Scope``.
    """
    payload = json.dumps({"type": task_class, "scope": sorted(scope)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


def sizing_key(spec: ChildSpec) -> str:
    """Content key for *spec*'s frozen estimate (see :func:`sizing_key_for`)."""
    return sizing_key_for(spec.type, spec.scope)


def _parse_sizing_marker(text: str) -> tuple[str, CostEstimate] | None:
    """The key and estimate carried by one ``[harness-sizing]`` comment, if it is one."""
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
        )
    except ValueError, TypeError, KeyError:
        return None
    return match.group(1), estimate


def frozen_estimates(repo_root: Path, feature_id: str) -> dict[str, CostEstimate]:
    """Estimates already frozen on *feature_id*, keyed by :func:`sizing_key`.

    First write wins: the earliest freeze is the verdict of record, so a stray
    later marker cannot re-open it. Best-effort — an unreadable bead or a
    malformed payload yields nothing and the caller recomputes.
    """
    frozen: dict[str, CostEstimate] = {}
    try:
        proc = _run_br(repo_root, ["comments", "list", feature_id, "--json"])
        comments = json.loads(proc.stdout)
    except RuntimeError, ValueError, OSError:
        return frozen
    if not isinstance(comments, list):
        return frozen
    for text in (str(c.get("text", "")) for c in comments if isinstance(c, dict)):
        parsed = _parse_sizing_marker(text)
        if parsed is not None:
            frozen.setdefault(parsed[0], parsed[1])
    return frozen


def forecast_for(repo_root: Path, task_class: str, scope: tuple[str, ...]) -> CostEstimate | None:
    """The estimate frozen anywhere in the tracker export for this content, or None.

    A shipped child cannot look up its own forecast by id: the governor freezes
    estimates on the *feature*, because when it runs no child exists yet. The key
    is content-derived, though, so the export answers without the parent link —
    and answers in a fresh clone, which is the point (basicly-kjc5.50).

    First match in export order wins, mirroring :func:`frozen_estimates`' rule that
    the earliest freeze is the verdict of record.
    """
    key = sizing_key_for(task_class, scope)
    for record in br.export_records(repo_root):
        for text in br.export_comment_texts(record):
            parsed = _parse_sizing_marker(text)
            if parsed is not None and parsed[0] == key:
                return parsed[1]
    return None


# --- Sizing carried into the dispatch record (basicly-jr0l.34) ----------------
#
# The forecast and the actual were written to disjoint classes of record: the
# governor's estimate was frozen on a feature at decompose, and the tokens a
# dispatch really spent landed on a run record that carried no forecast at all
# (`forecast_tokens` was a declared field with no writer — measured non-null on
# zero of 149 records). So the forecast error, which is the whole learning signal
# jr0l.21's calibration needs, has never once been computable. These are the
# inputs that put both halves on one carrier.

# Where a dispatch's forecast came from. A frozen estimate was registered before
# the work started and is evidence of prediction skill; one computed at dispatch
# is the same formula applied at the last honest moment, and a calibration must be
# able to tell them apart rather than average them together.
FROZEN_FORECAST = "frozen"
DISPATCH_FORECAST = "dispatch"


@dataclass(frozen=True)
class DispatchSizing:
    """One dispatch's task class and working-set forecast, resolved at dispatch time."""

    task_class: str
    estimate: CostEstimate
    # :data:`FROZEN_FORECAST` or :data:`DISPATCH_FORECAST`.
    source: str

    def record_inputs(self) -> dict[str, object]:
        """These inputs as ``record_dispatch`` keywords.

        On the sizing itself rather than at each dispatch site: both sites record
        the same four fields, and a second copy of this mapping is precisely how
        the two would drift apart (basicly-jr0l.16).
        """
        return {
            "scope_tokens": self.estimate.scope_tokens,
            "forecast_tokens": self.estimate.total,
            "task_class": self.task_class,
            "forecast_source": self.source,
        }


def dispatch_sizing(repo_root: Path, issue_id: str) -> DispatchSizing | None:
    """*issue_id*'s class and working-set forecast as of now, or None when unreadable.

    Prefers the estimate the governor froze for this content — the forecast of
    record, on :func:`frozen_estimates`' rule that the earliest freeze is the
    verdict — and otherwise computes one from the current calibrated factors, so a
    package that never went through ``decompose`` still yields a pairable forecast
    instead of a null.

    None when the bead's task class or declared ``## Scope`` cannot be read: a
    forecast against an unknown scope would be an invented number, and an absent
    half is what the error report is built to skip.
    """
    info = bead_class_and_scope(repo_root, issue_id)
    if info is None:
        return None
    task_class, scope = info
    frozen = forecast_for(repo_root, task_class, scope)
    if frozen is not None:
        return DispatchSizing(task_class, frozen, FROZEN_FORECAST)
    sizing = load_sizing_config(repo_root)
    estimate = CostEstimate(
        scope_tokens=scope_read_cost(repo_root, scope),
        overhead_tokens=instruction_overhead(repo_root),
        build_factor=build_factor_for(task_class, calibrated_build_factors(repo_root, sizing)),
    )
    return DispatchSizing(task_class, estimate, DISPATCH_FORECAST)


# --- Predicted spend beside the working set (basicly-jr0l.21) -----------------
#
# The working-set estimate above is an estimate of *context*, and context is not
# what a run costs (see `run_record`'s spend-forecast section for the measured
# 160-420x hole). So every sized package now also carries what it is predicted to
# spend — tokens, USD and wall clock — computed from the same working-set number
# through ratios calibrated per (model, task class), seeded from a declared prior
# until enough paired records exist to replace it.
#
# Alongside, never instead of: the band verdict still governs on the working set,
# because that is the quantity a context window bounds. Spend is the quantity a
# budget bounds, and basicly-jr0l.22 is the pass that will admit a lane against it.


def forecast_model(repo_root: Path) -> str | None:
    """The model a dispatch in this repo would run on now, or None when unresolved.

    The calibration key. Resolved from the configured default runner rather than
    passed in, so both the CLI and the loop get it without either having to
    remember — the same reason ``build_record`` stamps the session's config
    overrides centrally.

    PATH-only selection on purpose: ``select_runner``'s capability probe spawns the
    agent binary, and sizing a plan must not shell out to three CLIs. A probe-driven
    fallback to a different runner at build time therefore changes the model behind
    the forecast — visible afterwards, because the run record carries the model that
    actually ran (basicly-kjc5.59).

    None whenever the runner pins no model (a handoff runner has no model flag at
    all) or the config cannot be read. A null key measures against nothing and the
    declared prior stands, which is honest: nothing has been recorded for a model
    nobody can name.
    """
    try:
        config = load_runner_config(repo_root)
        spec = runner.select_runner(config.specs, config.default)
        return runner.resolve_model(spec, repo_root=repo_root).model
    except RuntimeError, ValueError, OSError:
        return None


@dataclass(frozen=True)
class SpendForecast:
    """What a package is predicted to spend, beside the working set it was sized on.

    A metric is None when nothing declares or measures the ratio behind it, and when
    the working set itself is zero — a package with no readable scope material. Both
    are indeterminate answers, and a zero cost would read as a free package.
    """

    tokens: int | None
    cost: float | None
    wall_clock_s: float | None
    # How each ratio was resolved, and the prior it was seeded from. Recorded with
    # the forecast so a seeded number can never be mistaken for a measured one.
    calibration: run_record.SpendCalibration

    @property
    def indeterminate(self) -> bool:
        """True when no metric could be predicted at all."""
        return self.tokens is None and self.cost is None and self.wall_clock_s is None


def forecast_spend(
    estimate: CostEstimate, calibration: run_record.SpendCalibration
) -> SpendForecast:
    """Predict tokens, USD and wall clock for *estimate* under *calibration*.

    Money and time are derived from the predicted tokens rather than from the
    working set, so the turn multiplier lives in exactly one ratio and the other two
    stay a price and a rate — each independently replaceable by measured history.
    """
    multiplier = calibration.tokens_per_working_set_token.value
    working_set = estimate.total
    tokens = round(working_set * multiplier) if multiplier is not None and working_set > 0 else None
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
    """Forecast each (task class, estimate) pair, reading history and model once.

    One history read for the whole batch: :func:`run_record.forecast_errors` walks
    the tracker export, and doing that per item would re-parse it for every one.

    Shared by the plan-shaped caller and the dispatch-shaped one so one package
    cannot be forecast two different ways depending on which gate is asking
    (basicly-jr0l.22) — the same single-estimator rule :func:`dispatch_sizing`
    already keeps for the working set.
    """
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


def spend_forecasts(
    repo_root: Path,
    children: tuple[ChildSpec, ...],
    estimates: tuple[CostEstimate, ...],
    sizing: SizingConfig,
) -> tuple[SpendForecast, ...]:
    """Forecast each planned child's spend (the decompose-time shape)."""
    pairs = tuple((spec.type, estimate) for spec, estimate in zip(children, estimates, strict=True))
    return _class_forecasts(repo_root, pairs, sizing)


def dispatch_spend_forecasts(
    repo_root: Path,
    sizings: tuple[DispatchSizing, ...],
    sizing: SizingConfig,
) -> tuple[SpendForecast, ...]:
    """Forecast each already-sized lane's spend (the dispatch-time shape).

    A :class:`DispatchSizing` already carries the two inputs a forecast needs, so a
    lane about to be dispatched is forecast from the very estimate that gates it
    rather than from a second one computed here (basicly-jr0l.22).
    """
    return _class_forecasts(
        repo_root, tuple((item.task_class, item.estimate) for item in sizings), sizing
    )


# The spend assumed for a lane whose scope cannot be read, when no measured lane
# actual exists yet to bound it with. Seeded from the first supervised lane this
# repo ever measured — 4079243 tokens for one leaf bug (basicly-jr0l.40,
# 2026-08-01) — and rounded down to a flat figure so it never reads as a
# measurement. Deliberately high: this feeds a *ceiling* check, where erring low
# admits an unbounded pass and erring high only asks a human to widen the grant or
# scope the bead. That asymmetry is the whole reason a seed is acceptable here.
UNSIZED_LANE_TOKENS_SEED = 4_000_000


def unsized_lane_tokens(repo_root: Path, sizing: SizingConfig) -> tuple[int, str]:
    """A conservative token bound for one unsizeable lane, and how it was derived.

    ``dispatch_sizing`` returns None for any bead with no ``## Scope`` heading, which
    is most hand-filed beads — and both dispatch cost gates consumed that None as
    "nothing to compare" and admitted. A lane with no forecast was therefore
    completely unbounded, which is how one lane spent 4079243 tokens against a
    3000000 ceiling (basicly-vz78).

    The bound does not need the scope. Calibration does — it divides tokens by scope
    read-cost to get a ratio — but a raw *actual* is already an observation of what a
    lane costs, whatever the tracker says about its scope. So this reads the measured
    lane actuals directly and takes the **maximum** over the recent window: a ceiling
    check wants a high-water mark, not a central estimate, and a median would admit
    the pass that the worst lane blows.

    The statistic is the **median of the most recent window**, and it is deliberately a
    central estimate rather than a worst case. Measured on this repo's own 13 lane
    actuals the population is bimodal — leaf lanes ran 856182 to 4079243 tokens while
    lane *packages* driving sub-tasks ran 7674671 to 20594047 — and nothing in a run
    record distinguishes the two, so a quantile high enough to bound a package sets
    every leaf's bound at a figure no realistic grant funds. Only leaves ever reach this
    gate (``ready_lanes`` excludes a lane with sub-tasks), so a max or p90 would refuse
    passes that genuinely fit, which is the ban on hand-filed work the old fail-open
    behaviour was protecting against.

    So this is one layer of three, and the only one that is an estimate:

    * **forward, here** — keeps a pass's *expected* total inside the grant. Best effort:
      a lane above the median can still overspend, and that is accepted.
    * **hard, per lane** — ``[runner] runner_timeout``. At the measured ~7850 tokens per
      second a 1800s cap bounds one lane near 14M tokens whatever this returns.
    * **hard, per session** — the retrospective halt in :func:`policy.spend_status`,
      which stops the *next* pass once recorded spend reaches the budget.

    Being an estimate is still decisive for the case that actually failed: one lane at
    4079243 measured tokens against a 3000000 remainder is refused, where the old gate
    admitted it and lost the money.

    Ordered by the record's own timestamp before the window is taken. Slicing the flat
    list would have windowed by whatever order the records file enumerates, which is
    per-bead and not chronological — so "recent" would not have meant recent.

    Returns ``(tokens, source)`` where *source* is ``"measured"`` or ``"seed"``, so a
    caller can report which it used and never present the seed as evidence.
    """
    records = run_record.load_run_records(repo_root) or {}
    observed: list[tuple[str, int]] = []
    for history in records.values():
        if not isinstance(history, list):
            continue
        for entry in history:
            if not isinstance(entry, dict):
                continue
            tokens = entry.get("tokens")
            # Adapter-reported only: a chars/4 estimate is not an observation, and a
            # helper or handoff dispatch is not a lane.
            if (
                entry.get("estimated") is False
                and entry.get("phase") == "lane"
                and isinstance(tokens, int)
                and tokens > 0
            ):
                observed.append((str(entry.get("timestamp", "")), tokens))
    if not observed:
        return UNSIZED_LANE_TOKENS_SEED, "seed"
    observed.sort()
    window = [tokens for _stamp, tokens in observed[-sizing.calibration_window :]]
    # `median_high` rather than `median`: it returns an actual sample rather than the
    # mean of the middle two, so the bound is always a spend some lane really incurred
    # and stays an int without a rounding rule to argue about.
    return statistics.median_high(window), "measured"


def freeze_estimate(
    repo_root: Path,
    feature_id: str,
    key: str,
    estimate: CostEstimate,
    spend: SpendForecast | None = None,
) -> None:
    """Record *estimate* under *key* on *feature_id* (best-effort, never fatal).

    *spend* is recorded beside it, prior and all: the marker is the only carrier
    that survives a clone, and a forecast whose seed is not written down cannot be
    audited once the seed is replaced. First write still wins, so re-governing the
    same plan later prints today's calibration but never rewrites the recorded one —
    the number of record is the one the plan was accepted on (D9).
    """
    payload = json.dumps(
        {
            "scope_tokens": estimate.scope_tokens,
            "overhead_tokens": estimate.overhead_tokens,
            "build_factor": estimate.build_factor,
            "total": estimate.total,
            "spend": None if spend is None else asdict(spend),
        },
        sort_keys=True,
    )
    with contextlib.suppress(RuntimeError, OSError):
        _run_br(
            repo_root,
            ["comments", "add", feature_id, f"{_SIZING_MARKER} key={key}\n{payload}"],
        )


@dataclass(frozen=True)
class PlanVerdict:
    """What the sizing governor makes of a plan, without acting on it (D8: estimate).

    Separated from :func:`govern_working_set` so the dry-run and the real run
    read the same numbers from the same code. Previously ``--dry-run`` called
    ``preview`` alone, so a plan could preview clean and then be refused on the
    real run — the preview was not a predictor of the thing it previews
    (basicly-u6tw).
    """

    estimates: tuple[CostEstimate, ...]
    # One guidance message per out-of-band child; empty when the plan is accepted.
    violations: tuple[str, ...]
    # Per-child sizing keys, and the frozen estimates that were reused, so the
    # governor can record only what it newly computed.
    keys: tuple[str, ...]
    frozen: dict[str, CostEstimate]
    # Predicted spend per child, alongside the working-set estimate that gates
    # (basicly-jr0l.21). Never consulted by the band check: a budget and a context
    # window bound different quantities.
    spend: tuple[SpendForecast, ...] = ()

    @property
    def refused(self) -> bool:
        """True when the real run would refuse this plan."""
        return bool(self.violations)


def estimate_plan(
    repo_root: Path, children: tuple[ChildSpec, ...], *, feature_id: str | None = None
) -> PlanVerdict:
    """Estimate every child and report band violations **without raising or recording**.

    The read-only half of the governor: same estimates, same band checks, same
    guidance strings as the real run, but nothing is refused and nothing is
    frozen. That makes it safe for ``--dry-run`` and makes divergence between the
    preview and the run impossible by construction rather than by discipline.

    The verdict also carries each child's predicted spend (basicly-jr0l.21), which
    the band check never reads — it is the number a budget is compared against, not
    a reason to refuse a plan.
    """
    sizing = load_sizing_config(repo_root)
    frozen = frozen_estimates(repo_root, feature_id) if feature_id is not None else {}
    factors = calibrated_build_factors(repo_root, sizing)
    overhead = instruction_overhead(repo_root)
    keys = tuple(sizing_key(spec) for spec in children)
    estimates = tuple(
        frozen.get(key) or estimate_cost(repo_root, spec, factors, overhead)
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
    """Estimate every child and refuse the plan on any band violation (D8: govern).

    Raises ``ValueError`` naming every violating child with its guidance (split
    above the ceiling, merge-with-sibling below the floor) so the agent can
    re-propose the whole plan in one round trip.

    With *feature_id*, the verdict is frozen against drift (basicly-kjc5.30): an
    estimate already recorded there is reused verbatim, and the ones computed here
    are recorded once the plan is accepted. Without it — a caller estimating a plan
    that has no bead yet — nothing is read or written and the estimate is a
    snapshot of this moment, as before.
    """
    verdict = estimate_plan(repo_root, children, feature_id=feature_id)
    if verdict.refused:
        raise ValueError(
            "sizing governor refused the decomposition:\n" + "\n".join(verdict.violations)
        )
    # Only an accepted plan is frozen: a refusal is the agent's cue to re-propose,
    # and freezing the numbers behind it would pin a verdict nothing acted on.
    if feature_id is not None:
        for key, estimate, spend in zip(
            verdict.keys, verdict.estimates, verdict.spend, strict=True
        ):
            if key not in verdict.frozen:
                freeze_estimate(repo_root, feature_id, key, estimate, spend)
    return verdict.estimates


# --- Recording in br --------------------------------------------------------


def _child_body(spec: ChildSpec) -> str:
    """Build a child issue body with the sections the DoR requires, plus ``## Scope``.

    Delegates the required-section set to :func:`policy.compose_body` rather than
    spelling out headings: the plan chooses the child's type, and a ``bug`` child
    also owes ``## Steps to Reproduce``. Hard-coding the ``task`` set here left a
    bug-typed child refused by its own classify gate (basicly-kjc5.44).

    A plan carries no reproduction steps, so a ``bug`` child's section arrives as
    a ``TODO`` for its lane agent to fill from the parent's context. That is the
    deliberate trade: the placeholder satisfies the gate structurally, so the
    fan-out proceeds and the bead says out loud what is still owed, where omitting
    the heading would instead wedge the child before anyone could supply it.
    """
    return policy.compose_body(
        spec.type,
        {
            "## Acceptance Criteria": "\n".join(f"- {item}" for item in spec.acceptance),
            "## Scope": "\n".join(f"- `{glob}`" for glob in spec.scope),
        },
    )


@dataclass(frozen=True)
class CreatedChild:
    """A recorded child issue plus its computed group and sibling dependencies."""

    issue_id: str
    spec: ChildSpec
    group: int
    depends_on: tuple[str, ...]


@dataclass(frozen=True)
class DecomposeResult:
    """The outcome of decomposing a feature into a recorded dependency graph."""

    feature_id: str
    children: tuple[CreatedChild, ...]
    # Issue ids per parallel group, in declared order — distinct groups are safe
    # to build concurrently; within a group the order is the serial build order.
    groups: tuple[tuple[str, ...], ...]

    @property
    def serial_order(self) -> tuple[str, ...]:
        """A valid topological order for the merge queue (declared order)."""
        return tuple(child.issue_id for child in self.children)

    @property
    def parallel_groups(self) -> int:
        """How many independently-buildable groups the feature decomposed into."""
        return len(self.groups)


def feature_labels(repo_root: Path, feature_id: str) -> tuple[str, ...]:
    """The labels on *feature_id*, to be inherited by each of its children.

    Read once per decomposition rather than once per child: an external ``br``
    invocation is ~175x an in-process read, and the answer cannot change
    mid-decomposition.
    """
    proc = _run_br(repo_root, ["show", feature_id, "--json"])
    payload = json.loads(proc.stdout)
    # ``br show --json`` returns a *list* of records, not a single object —
    # verified against the installed binary. A dict is tolerated too so this does
    # not become the only reader that breaks if that ever changes.
    record = payload
    if isinstance(payload, list):
        record = payload[0] if payload else {}
    raw = record.get("labels") or [] if isinstance(record, dict) else []
    return tuple(str(label) for label in raw if str(label).strip())


def _create_child(
    repo_root: Path, feature_id: str, spec: ChildSpec, labels: tuple[str, ...] = ()
) -> str:
    # A child inherits the parent's labels because phase membership is a label
    # rather than a re-parenting, so an unlabelled child is silently absent from
    # ``br list --label phase-N`` — the parent feature stays in the phase while
    # none of the work under it does (basicly-jr0l.26; the same root cause as
    # basicly-jr0l.25 on the overrun path). ``ChildSpec`` deliberately cannot
    # declare labels: letting an agent-authored plan re-declare them would let a
    # plan move work between phases.
    args = ["create", spec.title, "-t", spec.type, "--parent", feature_id]
    if labels:
        args += ["-l", ",".join(labels)]
    args += ["-d", _child_body(spec), "--json"]
    proc = _run_br(repo_root, args)
    return str(json.loads(proc.stdout)["id"])


def _assert_no_new_cycles(repo_root: Path, created_ids: set[str]) -> None:
    proc = _run_br(repo_root, ["dep", "cycles", "--blocking-only", "--json"])
    report = json.loads(proc.stdout)
    for cycle in report.get("cycles", []):
        members = set(cycle if isinstance(cycle, list) else cycle.get("issues", []))
        if members & created_ids:
            raise RuntimeError(f"decomposition introduced a dependency cycle: {sorted(members)}")


def decompose(repo_root: Path, feature_id: str, children: tuple[ChildSpec, ...]) -> DecomposeResult:
    """Create child issues under *feature_id* and wire the computed serial chains.

    The sizing governor runs first (D8): every child's context cost must land
    inside the configured working-set band or the whole plan is refused before
    anything is recorded. Each child is then created with acceptance criteria
    (so DoR passes) and the parent's labels (so it stays in the parent's phase),
    and any two children whose declared scopes overlap are serialized by a
    ``blocks`` chain in declared order. The resulting graph is checked for cycles
    before the result — carrying the parallel groups and serial order — is
    returned.
    """
    if not children:
        raise ValueError("decompose needs at least one child spec")

    govern_working_set(repo_root, children, feature_id=feature_id)
    groups = group_children(children)
    predecessors = chain_predecessors(groups)

    inherited = feature_labels(repo_root, feature_id)
    issue_ids = [_create_child(repo_root, feature_id, spec, inherited) for spec in children]

    created: list[CreatedChild] = []
    for index, spec in enumerate(children):
        pred = predecessors[index]
        depends_on: tuple[str, ...] = ()
        if pred is not None:
            pred_id = issue_ids[pred]
            _run_br(repo_root, ["dep", "add", issue_ids[index], pred_id, "-t", "blocks"])
            depends_on = (pred_id,)
        created.append(CreatedChild(issue_ids[index], spec, groups[index], depends_on))

    _assert_no_new_cycles(repo_root, set(issue_ids))

    grouped: dict[int, list[str]] = {}
    for child in created:
        grouped.setdefault(child.group, []).append(child.issue_id)
    group_tuples = tuple(tuple(grouped[g]) for g in sorted(grouped))

    return DecomposeResult(feature_id, tuple(created), group_tuples)


@dataclass(frozen=True)
class PlannedChild:
    """A child's computed placement before anything is recorded (for ``--dry-run``)."""

    spec: ChildSpec
    group: int
    predecessor: int | None


def preview(children: tuple[ChildSpec, ...]) -> tuple[PlannedChild, ...]:
    """Compute grouping and serial chains without touching ``br`` (pure)."""
    groups = group_children(children)
    predecessors = chain_predecessors(groups)
    return tuple(
        PlannedChild(spec, groups[index], predecessors[index])
        for index, spec in enumerate(children)
    )

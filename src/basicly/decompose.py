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

import fnmatch
import json
import re
import statistics
import tomllib
from dataclasses import dataclass
from pathlib import Path

from . import policy, run_record
from .br import run_br as _run_br
from .config import DEFAULT_BUILD_FACTOR, SizingConfig, load_sizing_config

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


def estimate_cost(
    repo_root: Path, spec: ChildSpec, factors: dict[str, float], overhead: int
) -> CostEstimate:
    """Estimate *spec*'s working-set cost from its declared scope and task class.

    An unlisted task class uses the ``task`` factor (the most conservative seed),
    falling back to :data:`DEFAULT_BUILD_FACTOR` when even that is absent.
    """
    factor = factors.get(spec.type, factors.get(DEFAULT_CHILD_TYPE, DEFAULT_BUILD_FACTOR))
    return CostEstimate(
        scope_tokens=scope_read_cost(repo_root, spec.scope),
        overhead_tokens=overhead,
        build_factor=factor,
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


def _bead_class_and_scope(repo_root: Path, bead_id: str) -> tuple[str, tuple[str, ...]] | None:
    """The task class and declared scope of *bead_id*, or None when unreadable."""
    try:
        proc = _run_br(repo_root, ["show", bead_id, "--json"])
        data = json.loads(proc.stdout)
    except RuntimeError, ValueError, OSError:
        return None
    issue = data[0] if isinstance(data, list) and data else data
    if not isinstance(issue, dict):
        return None
    task_class = issue.get("issue_type")
    description = issue.get("description")
    if not (isinstance(task_class, str) and task_class and isinstance(description, str)):
        return None
    scope = parse_scope_section(description)
    return (task_class, scope) if scope else None


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
    samples the seeds stand. Scope read-cost is recomputed against the current
    tree (the record does not persist it); an accepted approximation that sample
    volume averages out.
    """
    factors = dict(sizing.build_factors)
    records = run_record.load_run_records(repo_root)
    if not records:
        return factors

    samples: dict[str, list[tuple[str, float]]] = {}
    for bead_id, history in records.items():
        if not isinstance(history, list):
            continue
        reported = [
            entry
            for entry in history
            if isinstance(entry, dict)
            and entry.get("estimated") is False
            and isinstance(entry.get("tokens"), int)
            and entry["tokens"] > 0
        ]
        if not reported:
            continue
        info = _bead_class_and_scope(repo_root, bead_id)
        if info is None:
            continue
        task_class, scope = info
        cost = scope_read_cost(repo_root, scope)
        if cost <= 0:
            continue
        for entry in reported:
            timestamp = str(entry.get("timestamp", ""))
            samples.setdefault(task_class, []).append((timestamp, entry["tokens"] / cost))

    for task_class, class_samples in samples.items():
        recent = sorted(class_samples)[-sizing.calibration_window :]
        if len(recent) >= sizing.calibration_min_samples:
            factors[task_class] = statistics.median(factor for _, factor in recent)
    return factors


def govern_working_set(
    repo_root: Path, children: tuple[ChildSpec, ...]
) -> tuple[CostEstimate, ...]:
    """Estimate every child and refuse the plan on any band violation (D8: govern).

    Raises ``ValueError`` naming every violating child with its guidance (split
    above the ceiling, merge-with-sibling below the floor) so the agent can
    re-propose the whole plan in one round trip.
    """
    sizing = load_sizing_config(repo_root)
    factors = calibrated_build_factors(repo_root, sizing)
    overhead = instruction_overhead(repo_root)
    estimates = tuple(estimate_cost(repo_root, spec, factors, overhead) for spec in children)
    violations = [
        message
        for spec, estimate in zip(children, estimates, strict=True)
        if (
            message := policy.check_working_set(
                spec.title, estimate.total, estimate.scope_tokens, sizing
            )
        )
    ]
    if violations:
        raise ValueError("sizing governor refused the decomposition:\n" + "\n".join(violations))
    return estimates


# --- Recording in br --------------------------------------------------------


def _child_body(spec: ChildSpec) -> str:
    """Build a child issue body with the sections ``br lint`` DoR requires."""
    acceptance = "\n".join(f"- {item}" for item in spec.acceptance)
    scope = "\n".join(f"- `{glob}`" for glob in spec.scope)
    return f"## Acceptance Criteria\n\n{acceptance}\n\n## Scope\n\n{scope}\n"


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


def _create_child(repo_root: Path, feature_id: str, spec: ChildSpec) -> str:
    proc = _run_br(
        repo_root,
        [
            "create",
            spec.title,
            "-t",
            spec.type,
            "--parent",
            feature_id,
            "-d",
            _child_body(spec),
            "--json",
        ],
    )
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
    (so DoR passes), and any two children whose declared scopes overlap are
    serialized by a ``blocks`` chain in declared order. The resulting graph is
    checked for cycles before the result — carrying the parallel groups and
    serial order — is returned.
    """
    if not children:
        raise ValueError("decompose needs at least one child spec")

    govern_working_set(repo_root, children)
    groups = group_children(children)
    predecessors = chain_predecessors(groups)

    issue_ids = [_create_child(repo_root, feature_id, spec) for spec in children]

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

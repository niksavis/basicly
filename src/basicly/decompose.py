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


def bead_class_and_scope(repo_root: Path, bead_id: str) -> tuple[str, tuple[str, ...]] | None:
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
    samples the seeds stand.

    Scope read-cost comes from the record's own ``scope_tokens``, persisted by the
    dispatch that produced the sample (basicly-kjc5.30), so a sample means the same
    thing whenever it is read. Only a record written before that — which has no
    ``scope_tokens`` — falls back to measuring the current tree.
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
        info = bead_class_and_scope(repo_root, bead_id)
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


def sizing_key(spec: ChildSpec) -> str:
    """Content key for a spec's frozen estimate.

    Derived from exactly what the estimate is a function of — the task class picks
    the build factor and the declared scope sets the read cost. Deliberately not
    the title: retitling a child must not silently re-open a settled verdict.
    """
    payload = json.dumps({"type": spec.type, "scope": sorted(spec.scope)}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:12]


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
        head, _, payload = text.partition("\n")
        match = _SIZING_HEADER.match(head.strip())
        if match is None:
            continue
        try:
            data = json.loads(payload)
            estimate = CostEstimate(
                scope_tokens=int(data["scope_tokens"]),
                overhead_tokens=int(data["overhead_tokens"]),
                build_factor=float(data["build_factor"]),
            )
        except ValueError, TypeError, KeyError:
            continue
        frozen.setdefault(match.group(1), estimate)
    return frozen


def freeze_estimate(repo_root: Path, feature_id: str, key: str, estimate: CostEstimate) -> None:
    """Record *estimate* under *key* on *feature_id* (best-effort, never fatal)."""
    payload = json.dumps(
        {
            "scope_tokens": estimate.scope_tokens,
            "overhead_tokens": estimate.overhead_tokens,
            "build_factor": estimate.build_factor,
            "total": estimate.total,
        },
        sort_keys=True,
    )
    with contextlib.suppress(RuntimeError, OSError):
        _run_br(
            repo_root,
            ["comments", "add", feature_id, f"{_SIZING_MARKER} key={key}\n{payload}"],
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
    sizing = load_sizing_config(repo_root)
    frozen = frozen_estimates(repo_root, feature_id) if feature_id is not None else {}
    factors = calibrated_build_factors(repo_root, sizing)
    overhead = instruction_overhead(repo_root)
    keys = tuple(sizing_key(spec) for spec in children)
    estimates = tuple(
        frozen.get(key) or estimate_cost(repo_root, spec, factors, overhead)
        for spec, key in zip(children, keys, strict=True)
    )
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
    # Only an accepted plan is frozen: a refusal is the agent's cue to re-propose,
    # and freezing the numbers behind it would pin a verdict nothing acted on.
    if feature_id is not None:
        for key, estimate in zip(keys, estimates, strict=True):
            if key not in frozen:
                freeze_estimate(repo_root, feature_id, key, estimate)
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

    govern_working_set(repo_root, children, feature_id=feature_id)
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

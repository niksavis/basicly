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

One exception, and it is narrow: a child may declare part of its scope ``shared``
— a manifest or lockfile it only appends its own entry to — and overlap through a
path *both* sides declared shared does not serialize them. Without it, one shared
manifest made every child overlap every other and the transitive closure collapsed
a wholly parallel plan into a single chain (basicly-jr0l.45). Whichever way the
grouping lands, :func:`collapsing_paths` names the path it turned on.

The mirror case is a path no child declares at all, and it cost a rework budget
before it was closed (basicly-o8p0): a repo convention — a changelog entry per
landing — has *every* lane write one file, so it appears in no ``## Scope``, is
invisible to the grouping and to the band table, and three lanes with provably
disjoint scopes each inserted at the same anchor. Two landed and the third rebased
onto a moved anchor and conflicted. Such a path cannot be declared per bead, since
no bead mentions it, so it is declared once in ``[worktree] append_only_paths`` and
enters the grouping here as *contended*: it serializes two children unless both
declared it ``shared`` themselves, which is the same weakest-claim rule read from
the undeclared side.
"""

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

from . import br, plan_gate, plan_record, policy, run_record, runner
from .br import run_br as _run_br
from .config import (
    DEFAULT_BUILD_FACTOR,
    SizingConfig,
    load_runner_config,
    load_sizing_config,
    load_worktree_config,
)
from .read_cost import instruction_overhead, scope_read_cost

DEFAULT_CHILD_TYPE = "task"


# --- Plan model & parsing ---------------------------------------------------


@dataclass(frozen=True)
class ChildSpec:
    """One agent-proposed child track: a title, acceptance criteria, and file scope."""

    title: str
    acceptance: tuple[str, ...]
    scope: tuple[str, ...]
    type: str = DEFAULT_CHILD_TYPE
    # The subset of ``scope`` this child touches but does not own (see
    # :func:`_parse_shared`). Trailing and defaulted so every plan written before
    # the field existed keeps meaning exactly what it meant: own everything.
    shared: tuple[str, ...] = ()
    # The three fields the plan gate requires and this module records, each defaulting
    # to *absent* rather than to a value. ``None`` is the only honest default: an
    # invented budget or a guessed integrity level would pass the gate while meaning
    # nothing, and a dependency list defaulted to ``()`` would claim the plan declared
    # "nothing blocks this" when it declared nothing at all. Declared-empty is ``()``.
    depends_on: tuple[str, ...] | None = None
    budget_tokens: int | None = None
    integrity: str | None = None


def parse_children(data: object) -> tuple[ChildSpec, ...]:
    """Validate a parsed plan document into child specs.

    Expects ``{"children": [ {title, acceptance, scope, depends_on, budget_tokens,
    integrity, shared?, type?}, ... ]}``. Raises ``ValueError`` on any malformed entry
    rather than silently dropping a track — a lost child would be built by nobody.

    Two validations, in this order and deliberately not merged. Per entry, the *shape*:
    a field that is present must be the right type, and an entry that is wrong about
    that is wrong about itself, so it raises where it is read. Then the whole plan goes
    through :func:`plan_gate.require_plan`, which is where *absence* is judged — it can
    name every missing field across every child at once, where a per-entry raise would
    surface them one dispatch at a time. :class:`plan_gate.PlanGateError` is a
    ``ValueError``, so a caller that already handled a schema refusal handles this one.
    """
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
        integrity=_parse_integrity(entry.get("integrity"), where),
    )


def _parse_depends_on(value: object, where: str) -> tuple[str, ...] | None:
    """A child's declared dependency list: titles of siblings that must land first.

    Sibling *titles*, not issue ids, because the plan is written before anything is
    recorded and an id does not exist yet. :func:`decompose` resolves them once the
    children are created. An empty list is a declaration ("nothing blocks this") and
    is kept distinct from the key being absent, which the plan gate refuses.
    """
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
    """A child's declared token budget. ``bool`` is rejected: ``True`` is not 1 token."""
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"{where} 'budget_tokens' must be a whole number of tokens")
    return value


def _parse_integrity(value: object, where: str) -> str | None:
    """A child's declared integrity level, one of :data:`plan_gate.INTEGRITY_LEVELS`."""
    if value is None:
        return None
    if not isinstance(value, str) or not value.strip():
        raise ValueError(f"{where} 'integrity' must be a non-empty string")
    return value.strip()


def _string_list(value: object, where: str) -> tuple[str, ...]:
    if not (isinstance(value, list) and value):
        raise ValueError(f"{where} must be a non-empty list of non-empty strings")
    if not all(isinstance(v, str) and v.strip() for v in value):
        raise ValueError(f"{where} must be a non-empty list of non-empty strings")
    return tuple(v.strip() for v in value)


# Glob metacharacters :func:`globs_overlap` acts on. A shared declaration may
# contain none of them.
_WILDCARD_CHARS = "*?["


def _parse_shared(value: object, scope: tuple[str, ...], where: str) -> tuple[str, ...]:
    """Validate a child's ``shared`` list against its own declared *scope*.

    ``shared`` names the paths a child **touches but does not own** — a manifest, a
    lockfile, a package ``__init__`` that several children each append their own
    distinct entry to. Overlap through such a path does not serialize the children
    (see :func:`group_children`), which makes this the only thing a plan can declare
    that *removes* a ``blocks`` edge. So two rules keep it from hiding a real
    collision:

    * every entry must appear verbatim in ``scope``, so a shared declaration can only
      ever reclassify a path the plan already declared out loud — the recorded
      ``## Scope`` stays the whole truth for read-cost sizing and for merge-time
      attribution (:func:`merge.coupled_lanes`), and there is no way to smuggle in a
      path nothing else can see;
    * every entry must be a literal path, never a glob, so no plan can exempt a whole
      subtree from serialization behind one wildcard. The manifest case is always one
      named file; a wildcard is the case where nobody can say what is being shared.

    Absent (or an explicit empty list) means the child owns everything it declared,
    which is what every plan written before the field existed already meant.
    """
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


def serializes(
    a: ChildSpec,
    b: ChildSpec,
    *,
    ignoring: str | None = None,
    contended: tuple[str, ...] = (),
) -> bool:
    """True when two children must be serialized against each other.

    Overlap through a path that **both** children declared ``shared`` does not
    serialize them: each is appending its own distinct entry to a manifest neither
    owns, and a manifest is precisely the path a careful author is *most* likely to
    declare (basicly-jr0l.45). Overlap where either side owns its glob still
    serializes, so a shared declaration is only ever as strong as the weakest claim
    on the path — one child owning ``pyproject.toml`` still blocks every child that
    touches it, which is ccpm's designated-owner rule read from the other side.

    *contended* are the configured append-only paths (``[worktree]
    append_only_paths``) that this repo's conventions have every lane write and no
    plan declare. Each one serializes the pair by default rather than when it is
    declared, because "nobody mentioned it" is exactly the state the reported
    failure was in — and the escape hatch stays the same declaration as above: a
    child that has thought about the path puts it in its own ``scope`` *and*
    ``shared``, and two children that both did are left parallel.

    *ignoring* drops one declared glob from both sides, and the same path from
    *contended*. That is the counterfactual :func:`collapsing_paths` needs, and it
    is expressed here rather than by editing the scopes because a scope emptied by
    the edit would compare as "matches everything" and silently suppress the very
    split being measured.
    """
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
    """Union-find grouping over pairwise serialization (see :func:`group_children`).

    With *owned_only* every declared glob is treated as owned, which is the grouping
    as it stood before ``shared`` existed. :func:`collapsing_paths` measures against
    that view, because the collapse an author has to see is the one the paths
    themselves cause, whether or not a declaration already defused it.
    """
    # Reclassified once, not once per pair: `collapsing_paths` re-groups per candidate
    # glob, so a per-pair rewrite would be O(globs x children^2) allocations.
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
    """*spec* with its shared declarations dropped, i.e. owning its whole scope."""
    return spec if not spec.shared else replace(spec, shared=())


def group_children(
    children: tuple[ChildSpec, ...], contended: tuple[str, ...] = ()
) -> tuple[int, ...]:
    """Assign each child a group index; children that serialize share a group.

    Union-find over :func:`serializes`: the transitive closure is one group
    (serialized), while children that serialize against no group member stay
    separate (parallel-safe). Group indices are assigned by first-seen child so the
    numbering is deterministic and stable.

    The transitive closure is what makes one shared path expensive: before
    ``shared`` existed, four children that each declared ``pyproject.toml`` beside
    their own module all overlapped each other through that single file, so the
    closure merged them into one chain and serialized work that was otherwise
    entirely parallel (basicly-jr0l.45). Declaring the manifest ``shared`` in each of
    them removes that one edge and leaves the modules to decide.

    *contended* is the configured append-only path list (:func:`append_only_paths`),
    which serializes for the opposite reason: nobody declared it, so nobody chose it
    (basicly-o8p0). A caller that omits it groups the plan as if the repo declared no
    such convention, which is what every caller meant before the list existed.
    """
    return _group(children, owned_only=False, contended=contended)


def append_only_paths(repo_root: Path) -> tuple[str, ...]:
    """This repo's configured append-only paths, for the grouping to contend on.

    One named reader for ``[worktree] append_only_paths`` so every surface that
    groups a plan — the dry run, the real run, the loop's advance, preflight's
    contention warning — contends on the same list. A preview that grouped against a
    different list than the run would predict the wrong plan, which is the failure
    :func:`describe_collapsing_path` is centralized against one layer up.
    """
    return load_worktree_config(repo_root).append_only_paths


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


# --- Naming the path that collapses a plan (basicly-jr0l.45) ------------------
#
# The collapse above is silent, and silence is most of the damage: a plan whose
# scopes support four parallel groups reports one, with nothing saying which of the
# declared paths cost the other three. The author sees a serial chain and no reason
# for it, so the cheapest fix — a `shared` declaration on the one manifest — is the
# one nobody knows to make. So every decompose surface names the load-bearing path.


@dataclass(frozen=True)
class CollapsingPath:
    """One declared path whose overlaps are load-bearing for the grouping."""

    glob: str
    # Indices of the children that declared this exact glob, in declared order.
    declarers: tuple[int, ...]
    # Group counts with the path and with it dropped from every scope, both measured
    # with every glob treated as owned. The owned-only view on purpose: it is the
    # collapse the paths themselves cause, which is what an author has to see even
    # once a declaration has defused it.
    groups: int
    groups_without: int
    # True when the real (shared-respecting) grouping is unchanged by this path, i.e.
    # the declarations already stopped it collapsing anything.
    neutralized: bool


def collapsing_paths(
    children: tuple[ChildSpec, ...], contended: tuple[str, ...] = ()
) -> tuple[CollapsingPath, ...]:
    """The declared paths that merge parallel groups, most-collapsing first (pure).

    A path is load-bearing when dropping it from every scope would leave the plan in
    *more* parallel groups than it has — measured by re-grouping without it, so the
    answer accounts for the transitive closure rather than guessing from a pair.
    Candidates are the declared globs themselves, which keeps the diagnostic exactly
    as specific as the plan: it can name ``pyproject.toml`` or ``src/**``, and it
    reports every glob whose removal splits a group rather than picking one, because
    two globs can each be sufficient for the same collapse and an author needs to see
    both.

    ``neutralized`` distinguishes the path that *would* have collapsed the plan from
    the one that still does, so a plan that already declared the manifest ``shared``
    reads as informed rather than broken. Both are reported: the AC for jr0l.45 wants
    the collapsing path named whether or not it was defused, because a name is how a
    reviewer checks that the declaration was honest.

    A configured *contended* path is a candidate too, and usually the one candidate no
    child declared — so it reports empty ``declarers``, which is how
    :func:`describe_collapsing_path` tells the reader the path came from config
    rather than from the plan (basicly-o8p0). Naming it is the whole remedy: the
    collapse is otherwise a serial chain over scopes a reader can see are disjoint.

    A contended candidate is measured against the plan with the append-only
    convention off *entirely*, not merely with that one path off. Two configured
    paths are each independently sufficient for the collapse, so dropping one at a
    time would split nothing and report neither — the silence this diagnostic exists
    to remove. The cost is that with two of them the counterfactual count is the same
    for both, which is why the line reads "the declared scopes alone support N" rather
    than attributing that number to the one path it names.
    """
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
    """One report line for a path the *config* contends, not a child's scope."""
    origin = f"`{item.glob}`: append-only by convention ([worktree] append_only_paths)"
    if item.neutralized:
        return (
            f"{origin}, and every child that appends to it declared it 'shared', so it no longer "
            f"serializes the plan — {item.groups_without} group(s)"
        )
    # A contended path is usually in nobody's scope — that is the case it exists for —
    # but a child may also have declared it and *not* called it shared, which is a
    # claim of ownership. Both serialize; saying which is what tells the reader
    # whether the remedy is a declaration or a build order.
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
    """One report line naming *item* and what it does to the grouping.

    Formatted here rather than at each surface so the dry run, the real run and the
    loop's advance detail cannot describe the same collapse three different ways.

    *contended* is the caller's configured append-only list, so a path that entered
    the grouping from ``[worktree] append_only_paths`` rather than from a declared
    scope says where it came from (basicly-o8p0). Without that clause the line names
    a path the reader cannot find in any child's scope and reads as a bug in the
    grouping. It also carries a counterfactual measured against the convention being
    off rather than that one path (see :func:`collapsing_paths`), which is why the two
    branches word the counts differently.
    """
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
    """A one-line suffix naming the paths still collapsing the grouping, else ``""``.

    Empty when nothing collapses *and* when every collapsing path was already
    neutralized by a ``shared`` declaration: the grouping the caller is reporting is
    then the grouping the scopes support, and there is nothing to act on. The full
    report (:func:`describe_collapsing_path`) still names the neutralized ones.
    """
    live = [item.glob for item in collapsing if not item.neutralized]
    if not live:
        return ""
    return " — collapsing path(s): " + ", ".join(f"`{glob}`" for glob in live)


# --- Context-cost sizing estimator (basicly-kjc5.2, factory design D8) -------
#
# What a lane must *read* is measured in :mod:`basicly.read_cost` — this half turns
# those tokens into an estimate, a frozen verdict and a forecast.

# The heading _child_body records scope globs under; the line form itself belongs to
# plan_record, which owns every recorded section.
_SCOPE_HEADING = plan_record.SCOPE_HEADING


# Where a build factor came from. Recorded with every estimate, on the same rule
# `forecast_source`, `SpendCalibration` and `unsized_lane_tokens`' source already
# follow: a declared number must never be readable back as a measured one
# (basicly-tcmy.5).
#
# There is deliberately no ``measured`` member. Nothing in this engine measures a
# working-set factor: the calibration that appeared to was measuring whole-lane
# spend, a different quantity, and basicly-z2wi removed it (see the section below).
# A vocabulary offering the word would invite the next reader to assume a writer.
BUILD_FACTOR_SEED = "seed"  # config.DEFAULT_BUILD_FACTOR_SEEDS
BUILD_FACTOR_CONFIGURED = "configured"  # declared in [policy.sizing.build_factor]


@dataclass(frozen=True)
class CostEstimate:
    """One child's deterministic context-cost estimate (D8: estimate at decompose)."""

    scope_tokens: int
    overhead_tokens: int
    build_factor: float
    # :data:`BUILD_FACTOR_SEED` or :data:`BUILD_FACTOR_CONFIGURED`. Defaulted to the
    # seed so an estimate parsed back from a marker written before the field existed
    # keeps the provenance it actually had: at that time no calibration existed to
    # produce anything else.
    build_factor_source: str = BUILD_FACTOR_SEED

    @property
    def total(self) -> int:
        """Estimated working-set tokens: overhead + scope read-cost x build factor."""
        return self.overhead_tokens + round(self.scope_tokens * self.build_factor)


def _factor_key(task_class: str, factors: dict[str, float]) -> str:
    """Which entry of *factors* answers for *task_class*.

    Split out so the factor and its provenance are looked up by one rule: reading the
    value from one key and the source from another is how a configured number would
    end up recorded as a seed.
    """
    return task_class if task_class in factors else DEFAULT_CHILD_TYPE


def build_factor_for(task_class: str, factors: dict[str, float]) -> float:
    """The build factor for *task_class*.

    An unlisted task class uses the ``task`` factor (the most conservative seed),
    falling back to :data:`DEFAULT_BUILD_FACTOR` when even that is absent. Shared by
    :func:`estimate_cost` and :func:`dispatch_sizing` so a plan's estimate and the
    same package's dispatch-time forecast cannot be computed two different ways.
    """
    return factors.get(_factor_key(task_class, factors), DEFAULT_BUILD_FACTOR)


def build_factor_source(task_class: str, sizing: SizingConfig) -> str:
    """Where *task_class*'s build factor came from (:data:`BUILD_FACTOR_SEED` etc).

    Keyed on the entry that actually answered, so a class with no factor of its own
    inherits ``task``'s provenance rather than being reported as seeded while a
    configured ``task`` factor is what sized it.
    """
    key = _factor_key(task_class, sizing.build_factors)
    return BUILD_FACTOR_CONFIGURED if key in sizing.configured_build_factors else BUILD_FACTOR_SEED


def estimate_cost(
    repo_root: Path, spec: ChildSpec, sizing: SizingConfig, overhead: int
) -> CostEstimate:
    """Estimate *spec*'s working-set cost from its declared scope and task class.

    Takes the whole :class:`SizingConfig` rather than its factor map: the estimate
    now carries where its factor came from, and that provenance lives beside the
    factors in the config rather than in the map itself.
    """
    return CostEstimate(
        scope_tokens=scope_read_cost(repo_root, spec.scope),
        overhead_tokens=overhead,
        build_factor=build_factor_for(spec.type, sizing.build_factors),
        build_factor_source=build_factor_source(spec.type, sizing),
    )


def parse_scope_section(description: str) -> tuple[str, ...]:
    """The scope globs recorded under a ``## Scope`` heading, as _child_body writes them.

    Delegates the reading to :func:`plan_record.backticked_entries`, so the section
    reader the build entry predicate uses and the one every sizing and merge gate uses
    are the same code — two readers of one recorded form is how the form drifts.
    """
    return plan_record.backticked_entries(description, _SCOPE_HEADING)


def unparsed_scope_warning(description: str) -> str | None:
    """What to tell an author whose ``## Scope`` heading yielded no readable glob.

    Heading present, entries absent. That is almost always an authoring error rather
    than a deliberate empty scope, and nothing downstream can tell the two apart:
    :func:`parse_scope_section` returns an empty tuple for both, so the bead sizes,
    groups and lands exactly as one that never declared a scope at all — while its
    author reads the heading back and believes the lane is sized (basicly-tuy6).

    Detecting it needs the heading, which the parser discards, so the check lives
    here beside the pattern rather than in the gates that consume the result.

    Returns None when there is nothing to say: no heading (the ordinary state of a
    bead nobody decomposed, and not an error), or a heading that parsed. **Advisory
    by construction** — it returns prose, never a verdict. Refusing on it would
    block most of an existing tracker, which is the objection that settled
    basicly-vz78.
    """
    if not any(line.strip() == _SCOPE_HEADING for line in description.splitlines()):
        return None
    if parse_scope_section(description):
        return None
    return (
        f"the `{_SCOPE_HEADING}` section parsed to no globs, so every gate reading it "
        "treats this bead as declaring no scope at all — its lane cannot be sized and "
        "the landing scope check is inert. Write each entry as a backticked glob on "
        f"its own line, e.g. {policy.SCOPE_LINE_EXAMPLE}"
    )


# Why a bead yields no class-and-scope pair. One None used to answer for both, and
# the sizing gates read that None as "nothing to compare" and admitted — so the band
# never once looked at a hand-filed bead (basicly-jr0l.60). The two are not the same
# answer:
#
# * **unreadable** — the record did not come back, or came back without the fields.
#   Transient, and it says nothing at all about the lane's size.
# * **undeclared** — the record read fine and carries no scope
#   :func:`parse_scope_section` can read: no ``## Scope`` heading, or one whose entries
#   are prose rather than backticked globs. Structural, and the normal state of a bead
#   nobody decomposed. A gate can act on it, because re-reading will not change it.
SCOPE_UNREADABLE = "unreadable"
SCOPE_UNDECLARED = "undeclared"
# * **greenfield** — the record read fine and its globs are well formed, but every one
#   of them matches nothing on disk, so :func:`scope_read_cost` returns zero and the
#   only forecast left is pure overhead. Structural like *undeclared*, and it carries
#   the same epistemic weight: a forecast against a scope that does not exist yet is
#   the "invented number" :func:`resolve_dispatch_sizing` already refuses to produce
#   for a bead declaring no scope at all (basicly-jr0l.69).
#
#   Measured 2026-08-06, which is what promoted this from a nicety to a gate failure.
#   Reading cost is a sound proxy for a lane that *edits* files and inverts for one
#   that *creates* them — writing a module and its tests from nothing is the expensive
#   case, not the cheap one:
#
#     bead        recorded forecast   actual spend   ratio
#     vkh0.13              657033      13367072     20.3x   <- broke the 10x band
#     vkh0.12              657033       7730640     11.8x
#
#   Against the measured unsized-lane bound instead, every lane of that wave lands
#   inside the band at 1.24x-3.98x, which is why the answer is to route these to that
#   bound rather than to widen the band.
SCOPE_GREENFIELD = "greenfield"


def _read_class_and_scope(issue: object) -> tuple[tuple[str, tuple[str, ...]] | None, str]:
    """One issue record's class-and-scope pair, plus which absence explains a missing one.

    The absence is :data:`SCOPE_UNREADABLE` or :data:`SCOPE_UNDECLARED`, and empty
    when the pair is there.
    """
    if not isinstance(issue, dict):
        return None, SCOPE_UNREADABLE
    task_class = issue.get("issue_type")
    description = issue.get("description")
    if not (isinstance(task_class, str) and task_class and isinstance(description, str)):
        return None, SCOPE_UNREADABLE
    scope = parse_scope_section(description)
    if not scope:
        return None, SCOPE_UNDECLARED
    return (task_class, scope), ""


def _read_bead(repo_root: Path, bead_id: str) -> tuple[tuple[str, tuple[str, ...]] | None, str]:
    """*bead_id*'s class-and-scope pair from the tracker, with the absence that explains it."""
    # The seam's None covers every absence this used to catch by exception type — br
    # off PATH, a non-zero exit, unparseable output, an empty or non-object payload —
    # so the typed absence is kept while the unwrap is not spelled again
    # (basicly-tcmy.14).
    record = br.read_record(repo_root, bead_id)
    if record is None:
        return None, SCOPE_UNREADABLE
    return _read_class_and_scope(record)


def bead_class_and_scope(repo_root: Path, bead_id: str) -> tuple[str, tuple[str, ...]] | None:
    """The task class and declared scope of *bead_id*, or None when either is absent.

    Callers that must tell an unreadable bead from one declaring no scope want
    :func:`resolve_dispatch_sizing` — this collapses both to None on purpose, for the
    callers (grouping, coupling, merge) to which an absent scope is simply an empty one.
    """
    return _read_bead(repo_root, bead_id)[0]


# --- Why there is no measured build factor (basicly-z2wi) --------------------
#
# `build_factor` answers "how big is a lane's working set, per token of scope it
# must read". The seeds above are that shape. A calibration used to overwrite them
# with `whole-lane spend / scope read-cost`, and that is a different quantity: a
# lane's total spend already contains the turn multiplier, which `forecast_spend`
# owns as `tokens_per_working_set_token` and which its docstring requires to live
# in exactly one ratio. Calibrating from spend put it in two, so the forecast
# multiplied it in twice and the band compared a spend number against
# `working_set_max`, a context-window ceiling.
#
# It was not a rounding error. On this repo's own history the task factor
# calibrated to 216.65 against a seed of 3.0, which caps the largest dispatchable
# scope at ~295 tokens and refused every task-typed child in the band. Ten
# successful dispatches are what crossed `calibration_min_samples` and disabled
# the gate, so the engine broke itself by being used.
#
# No run record carries a working-set measure — the fields are `tokens`, `cost`
# and `duration_s` — so there is nothing to calibrate this against without new
# telemetry. Until such a measure is recorded, the seeds stand. Do not reintroduce
# a factor derived from spend; `test_no_working_set_factor_is_derived_from_spend`
# fails if one is.


# --- Frozen estimates (basicly-kjc5.30, design D9) ---------------------------
#
# D8 calls the sizing estimate deterministic, and it is — per invocation. Across
# invocations it still drifts, because scope read-cost is measured against the tree
# as it stands. So `govern_working_set` can refuse today the very plan it accepted
# last week, with neither the plan nor the code touched. D9 forbids a gate whose
# verdict depends on when it ran. (The build factor was a second drift source until
# basicly-z2wi removed its calibration; the seeds are constants and do not move.)
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
            # Absent on every marker frozen before the field existed, and the default
            # is the truth for those: the seeds were the only source there had ever
            # been. Required would instead discard the frozen verdict and recompute,
            # which is the drift the freeze exists to prevent (D9).
            build_factor_source=str(data.get("build_factor_source", BUILD_FACTOR_SEED)),
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

    def record_inputs(self, repo_root: Path) -> dict[str, object]:
        """These inputs as ``record_dispatch`` keywords.

        On the sizing itself rather than at each dispatch site: both sites record
        the same fields, and a second copy of this mapping is precisely how
        the two would drift apart (basicly-jr0l.16).

        The build factor's provenance travels with the forecast it produced
        (basicly-tcmy.5). Without it the record carries a number multiplied by a
        declared constant and no statement that it was declared, which is the state
        every sibling field on the record was designed not to be in.

        Takes *repo_root* for one field: the forecast **spend**, which needs the
        calibration this repo's history resolves. A lane's actual is metered in spend,
        so recording only ``forecast_tokens`` left the pair comparing a working set
        against a whole-lane cost — a 64x-793x ratio that read as estimator error
        (basicly-tcmy.34). Both are recorded, and the spend half is the very number
        :func:`supervise.admit_pass_spend` refuses a pass on.

        Never raises: the calibration reads the tracker, and this is telemetry on the
        critical path of every dispatch. An unreadable history records a null spend
        forecast, which is what it is.
        """
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
    """A lane's dispatch sizing, or which absence explains it having none.

    The pair exists because a gate has to act differently on the two absences
    :data:`SCOPE_UNREADABLE` and :data:`SCOPE_UNDECLARED` (basicly-jr0l.60), and a
    bare None could not tell them apart.
    """

    sizing: DispatchSizing | None
    # "" when *sizing* is there; else SCOPE_UNREADABLE or SCOPE_UNDECLARED.
    absence: str = ""


def resolve_dispatch_sizing(repo_root: Path, issue_id: str) -> SizingLookup:
    """*issue_id*'s class and working-set forecast as of now, or why there is none.

    Prefers the estimate the governor froze for this content — the forecast of
    record, on :func:`frozen_estimates`' rule that the earliest freeze is the
    verdict — and otherwise computes one from the current calibrated factors, so a
    package that never went through ``decompose`` still yields a pairable forecast
    instead of a null.

    Unsized when the bead's task class or declared ``## Scope`` is absent: a forecast
    against an unknown scope would be an invented number, and an absent half is what
    the error report is built to skip. Which absence it was travels in
    :attr:`SizingLookup.absence`, because a bead that declares no scope is a fact a
    gate can act on and a failed read is not.

    Unsized on the same grounds when every declared glob matches nothing
    (:data:`SCOPE_GREENFIELD`, basicly-jr0l.69). That case used to yield an
    overhead-only forecast, which is the very invented number the paragraph above
    refuses — and it is invented in the *dangerous* direction, because a lane creating
    a module and its tests from nothing is the expensive case. Measured: two lanes
    forecast at 657033 spent 13367072 and 7730640, breaking the 10x accuracy band.
    """
    info, absence = _read_bead(repo_root, issue_id)
    if info is None:
        return SizingLookup(None, absence)
    task_class, scope = info
    # Checked before the frozen estimate is honoured, not after: the freeze records
    # whatever the scope read at decompose time, so a child whose files did not exist
    # then carries a frozen overhead-only number that looks like a prediction of record
    # (`forecast_source` reads `frozen`) while resting on nothing. Rejecting it here is
    # what makes the accuracy gate's `assumed:` exclusion reach these records.
    if scope and scope_read_cost(repo_root, scope) == 0:
        return SizingLookup(None, SCOPE_GREENFIELD)
    frozen = forecast_for(repo_root, task_class, scope)
    if frozen is not None:
        return SizingLookup(DispatchSizing(task_class, frozen, FROZEN_FORECAST))
    sizing = load_sizing_config(repo_root)
    estimate = CostEstimate(
        scope_tokens=scope_read_cost(repo_root, scope),
        overhead_tokens=instruction_overhead(repo_root),
        build_factor=build_factor_for(task_class, sizing.build_factors),
        build_factor_source=build_factor_source(task_class, sizing),
    )
    return SizingLookup(DispatchSizing(task_class, estimate, DISPATCH_FORECAST))


def dispatch_sizing(repo_root: Path, issue_id: str) -> DispatchSizing | None:
    """*issue_id*'s class and working-set forecast as of now, or None when unsized.

    The sizing alone, for callers that record a forecast rather than gate on one.
    A caller that gates wants :func:`resolve_dispatch_sizing`, whose absence says
    whether re-reading could change the answer.
    """
    return resolve_dispatch_sizing(repo_root, issue_id).sizing


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


def spend_from_working_set(
    working_set: int, calibration: run_record.SpendCalibration
) -> int | None:
    """Predicted whole-lane spend for a lane holding *working_set* tokens of context.

    The one place the turn multiplier is applied, so the working-set unit is converted
    to the spend unit by exactly one rule: :func:`forecast_spend` predicts a package
    about to run and :func:`spend_accuracy` re-derives the same number for a package
    that already ran, and a second copy of the arithmetic is how those two would come
    to disagree about what was forecast (basicly-tcmy.34).

    None when nothing declares or measures the multiplier, and when the working set is
    zero — a package with no readable scope material. Both are indeterminate, and a
    zero would read as a free package.
    """
    multiplier = calibration.tokens_per_working_set_token.value
    if multiplier is None or working_set <= 0:
        return None
    return round(working_set * multiplier)


def forecast_spend(
    estimate: CostEstimate, calibration: run_record.SpendCalibration
) -> SpendForecast:
    """Predict tokens, USD and wall clock for *estimate* under *calibration*.

    Money and time are derived from the predicted tokens rather than from the
    working set, so the turn multiplier lives in exactly one ratio and the other two
    stay a price and a rate — each independently replaceable by measured history.
    """
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


# --- Is the sizing measured yet? (basicly-tcmy.5) -----------------------------
#
# Every sizing number this engine forecasts with is currently declared: the build
# factors are seeds by design (basicly-z2wi), and the spend ratios stand on
# `DECLARED_SPEND_PRIOR` until a class has `calibration_min_samples` paired write
# dispatches on the model in use. Each of those facts is recorded where it is
# produced, and none of them was ever *reported* — so "is the forecast measured
# yet?" was a question an operator answered by reading source, and the honest
# answer ("no, and here is how far off it is") looked identical to a silence.
#
# Preflight is where it belongs: before a budget is minted is the only moment the
# answer can still change a decision (the basicly-prnm stance for the band table).


@dataclass(frozen=True)
class CalibrationStatus:
    """How much of the sizing forecast is measured, per task class.

    Two independent numbers, deliberately kept apart: *samples* is the paired history
    behind the **spend** ratios, and *build_factor_sources* is where each **working
    set** factor came from. They are different quantities with different calibrations
    — conflating them is what basicly-z2wi's 216x was — so a report may not collapse
    them into one "calibrated" flag.
    """

    # The model a dispatch would run on now; None when the runner pins none, and then
    # nothing can be measured at all because a sample cannot join a key.
    model: str | None
    min_samples: int
    # Eligible paired samples per task class, for *model*.
    samples: dict[str, int]
    # :data:`BUILD_FACTOR_SEED` or :data:`BUILD_FACTOR_CONFIGURED`, per task class.
    build_factor_sources: dict[str, str]

    @property
    def measured_classes(self) -> tuple[str, ...]:
        """Classes with enough paired history to replace the prior's turn multiplier.

        The multiplier specifically: it is measured from every pair, while the price and
        the rate are measured only from the pairs whose adapter also metered money or
        time, so a class counted here can still be seeded in USD.
        """
        return tuple(
            name for name, count in sorted(self.samples.items()) if count >= self.min_samples
        )

    @property
    def on_seeds(self) -> bool:
        """True while no class has enough history to measure a spend ratio."""
        return not self.measured_classes


def calibration_status(repo_root: Path, sizing: SizingConfig) -> CalibrationStatus:
    """Report whether the sizing forecast is still standing on declared numbers.

    Read-only and best-effort, like every other reader of the record stream: an
    unreadable history counts as no samples, which is what it is.

    The classes reported are the ones a factor is declared for, unioned with any class
    the history has samples for — so a repo that sizes a class nobody configured still
    sees its history, and a configured class with no history still shows as zero rather
    than being absent.
    """
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
    lane actuals directly, over the most recent window.

    The sample set is every dispatch recorded under :data:`run_record.WRITE_PHASES` —
    both the supervised lane and the interactive build, because they are the same kind
    of work and the bound is a statement about what that work costs. It required
    ``phase == "lane"`` alone until basicly-tcmy.5, and the interactive path is the
    documented default on a single-operator machine: on this repo's own history that
    filter saw 24 of the 32 adapter-metered write dispatches and bounded a lane at
    15245717 tokens where the whole population gives 15830484. A helper dispatch (a
    rubric judge, the decider) is still excluded, and so is one whose phase was never
    recorded — neither is evidence of what a lane costs.

    The statistic is the **quantile at** ``[policy.sizing] unsized_lane_quantile``
    (default 0.9), because this is a *ceiling* and a ceiling wants a high-water mark.
    It replaced a median, which was chosen when the recorded population looked bimodal
    — leaves apparently running 856182 to 4079243 tokens and lane *packages* driving
    sub-tasks running 7674671 to 20594047, with nothing in a run record telling them
    apart, so any high quantile would have bounded a leaf at a package's cost.

    **That split did not survive contact with more data** (basicly-jr0l.58). Four leaf
    lanes measured 9418977, 10834801, 11478450 and 11867602 tokens — squarely inside
    the supposed package band — so the population is not two clusters but one wide
    spread from 856182 to 20594047. The median was therefore not a central estimate of
    a tight cluster but the midpoint of an order-of-magnitude range: 47% of the 17
    recorded actuals exceeded it, and a pass of four lanes forecast at 16316972 tokens
    spent 43599830 against a 21000000 grant.

    A high quantile costs throughput when it is wrong, and money when it is not there
    at all. The asymmetry favours the quantile: a refused pass costs one re-grant,
    while an admitted overrun doubled a real bill.

    This remains one layer of three, and the only one that is an estimate:

    * **forward, here** — keeps a pass's forecast total inside the grant, now aiming to
      be exceeded by at most one lane in ten rather than by half of them.
    * **hard, per lane** — ``[runner] runner_timeout``. Note the rate is not the ~7850
      tokens per second previously recorded here: measured rates rise with duration
      (4377/s at 196s up to 13872/s at 1485s) as context accumulates, so a 1800s cap
      bounds one lane nearer 25M than 14M.
    * **hard, per session** — the retrospective halt in :func:`policy.spend_status`,
      which stops the *next* pass once recorded spend reaches the budget.

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
    """The sample at *quantile* of *values*; always a figure some lane really incurred.

    An observed sample rather than an interpolation, for the reason ``median_high``
    was used before it: the bound is then always a spend that really happened, and
    stays an int with no rounding rule to argue about.

    Rounds *up* to the sample index, so the result sits at or above the requested
    quantile. Rounding down would quietly return a weaker bound than asked for, and
    that is the direction that costs money.
    """
    if not values:
        raise ValueError("a quantile needs at least one sample")
    ordered = sorted(values)
    index = math.ceil(quantile * len(ordered)) - 1
    return ordered[max(0, min(index, len(ordered) - 1))]


# --- Is the spend forecast within an order of magnitude? (basicly-tcmy.34) ----
#
# A grant is minted from a forecast, so a forecast wrong by orders of magnitude is a
# grant sized wrong: basicly-gczc was dispatched under an 8_000_000-token L3 grant,
# spent 16_963_245, and the halt landed the ship checkpoint on a human after the work
# was already done. Every step of the engine behaved correctly; the number was wrong.
#
# It was wrong by *unit*, not by calibration. The recorded forecast was a working set
# and the recorded actual is whole-lane spend, and this is the check that holds the two
# same-unit numbers against each other instead. Measured over the 26 comparable write
# dispatches in this repo's committed ledger, the spend forecast lands at 0.19x-2.37x
# of actual (median 0.94x) — inside one order of magnitude, both directions, with
# nothing excluded but the one record below. The same records' working-set forecasts
# sat at 64x-793x of the same actuals, which is the whole of the reported defect.
#
# Two directions, because a forecast has two failure modes and only one of them is the
# one that hurt: under-forecasting spends money a grant did not admit, over-forecasting
# refuses a pass that would have fitted. A band, therefore, rather than a ceiling.
#
# **One class of record cannot answer, and is counted rather than dropped.** A recorded
# working-set forecast above `working_set_max` was not produced by the estimator this
# check is about: basicly-tcmy.31 carries 6_762_766 against a scope read-cost of
# 35_106, a factor of ~193 from the spend-derived calibration basicly-z2wi removed. Its
# derived spend forecast is 2.26 billion tokens against 8_574_169 actual (0.004x), and
# that is a measurement of a code path that no longer exists. The rule is the band's own
# ceiling rather than the bead's name, so it stays checkable — and
# :attr:`SpendAccuracy.incomparable` names every record it silences, because a
# population quietly shrunk by a filter is how basicly-ipx2 committed a false claim.

# How far actual spend may sit from its forecast before the forecast is not a forecast:
# one order of magnitude, either way.
SPEND_RATIO_BAND = 10.0

# Where a pair's forecast came from. `recorded` is the number the dispatch itself
# wrote down (available since basicly-tcmy.34); `derived` re-applies today's
# calibration to the working set an older dispatch recorded, so the check binds on the
# history that already exists instead of only on records written from now on — a gate
# that measures an empty set is indistinguishable from a passing one.
RECORDED_SPEND_FORECAST = "recorded"
DERIVED_SPEND_FORECAST = "derived"


@dataclass(frozen=True)
class SpendPair:
    """One *bead's* forecast whole-lane spend beside the spend it really incurred.

    Both halves in tokens of spend, which is what makes it a pair. The working-set
    forecast and its measured occupancy are the *other* pair
    (``run_record.ForecastError``, ``RunRecord.context_tokens``); mixing one half of
    each is the defect this exists to close.

    The unit on the actual side is the bead, not the dispatch (basicly-u2hl.15).
    ``forecast_spend_tokens`` is derived from the bead's *scope*, so every dispatch of a
    bead records the identical number — what getting that bead done should cost — and
    scoring each attempt against it made every re-dispatch a structural under-spend,
    since the forecast covers work an earlier attempt already did. `basicly-u2hl.14` ran
    30,139,416 then 2,785,270 then 1,512,403 tokens against a 26,320,290 forecast: the
    third attempt alone read as 0.057x and turned main red, while the lane came in at
    1.31x. :attr:`actual_tokens` therefore sums the attempts, which is also the unit a
    grant is minted in — a grant pays for the rework too.
    """

    bead: str
    timestamp: str
    forecast_tokens: int
    actual_tokens: int
    # :data:`RECORDED_SPEND_FORECAST` or :data:`DERIVED_SPEND_FORECAST`.
    basis: str
    task_class: str | None = None
    model: str | None = None
    # How many dispatches :attr:`actual_tokens` sums. Reported rather than folded away:
    # a lane that took three attempts to land is a fact about the work, and a population
    # quietly merged is the shape `incomparable` and `unscoped` already exist to refuse.
    attempts: int = 1

    @property
    def ratio(self) -> float:
        """Actual over forecast. One quantity on both sides, so 1.0 is a perfect call."""
        return self.actual_tokens / self.forecast_tokens

    @property
    def spent(self) -> str:
        """The actual, naming the dispatch count whenever it took more than one."""
        over = f" over {self.attempts} dispatches" if self.attempts > 1 else ""
        return f"{self.actual_tokens:,} tokens{over}"

    @property
    def in_band(self) -> bool:
        """True while the forecast is within :data:`SPEND_RATIO_BAND` of the actual."""
        return 1 / SPEND_RATIO_BAND <= self.ratio <= SPEND_RATIO_BAND


@dataclass(frozen=True)
class SpendAccuracy:
    """Every bead whose forecast spend can be held to its actual, and what cannot."""

    pairs: tuple[SpendPair, ...] = ()
    # Metered write dispatches carrying no forecast of either kind: a lane whose bead
    # declares no readable scope was never sized, so there is nothing to hold it to.
    #
    # Named `unsized` rather than `unforecast` deliberately: `wired_or_deleted` reports an
    # unread record field by name, so reusing a name already baselined on another class
    # (`supervise.PassSpendAdmission.unforecast`) would make that finding read as fixed.
    unsized: int = 0
    # Beads whose recorded working-set forecast the band itself would refuse, so no
    # spend forecast can be derived from it. Named, never counted silently.
    incomparable: tuple[str, ...] = ()
    # Dispatches with no adapter-measured actual: a handoff, a killed run, or a chars/4
    # transcript estimate, which is a floor on spend rather than a measurement of it.
    unmetered: int = 0
    # Dispatches the runner reported as `failed`. Their tokens are what the attempt
    # spent before dying — `basicly-5xcj` exited 1 after 33,880 tokens of startup — so
    # holding them to a whole-lane forecast measures the abort, not the work. Counted
    # and reported, never dropped: a filter on an optional field hides a population,
    # and the failures are the half a naive query would silently lose (`basicly-ipx2`).
    aborted: int = 0
    # Beads whose forecast came from the `assumed:` fallback rather than a declared
    # scope. That number is the measured whole-lane quantile standing in for a bead the
    # estimator could not size, so it is a placeholder, not a prediction — comparing it
    # is the z2wi shape again, a number held against a quantity it does not denominate.
    # `basicly-sco6` declares no scope, was assumed at 16,576,875, and spent 1,218,172.
    #
    # Named `unscoped` rather than `assumed` for the reason the sibling field above
    # records: `wired_or_deleted` keys an unread record field by name, and
    # `supervise.PassSpendAdmission.assumed` is already baselined, so reusing it made
    # that finding read as fixed. The gate caught it on the first full run.
    unscoped: tuple[str, ...] = ()

    @property
    def median_ratio(self) -> float | None:
        """Median actual/forecast across the pairs; None with no pairs.

        A median for the reason ``ForecastErrorReport.median_ratio`` gives: one sample
        from the tail of a wide spread drags a mean where no dispatch has ever been.
        """
        if not self.pairs:
            return None
        return statistics.median(pair.ratio for pair in self.pairs)

    @property
    def violations(self) -> tuple[str, ...]:
        """Every pair outside the band, worst first, naming what a grant would get wrong.

        Empty is the passing state, and :attr:`pairs` is what says whether that emptiness
        was measured or merely unpopulated.
        """
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
    """Every comparable dispatch of one bead as the single lane its forecast denominates.

    The forecast is the *last* attempt's, and its basis with it: a re-dispatch re-reads
    the bead, so four of the eight multiply-dispatched beads in this repo's ledger carry
    forecasts that differ across their attempts — by 2.5% to 9.7% — and the newest is the
    one a re-grant would be sized from. A tie-break, not a lever: each of the four lands
    in band under either end of its own spread, so no verdict here turns on the choice.
    """
    last = attempts[-1]
    return replace(
        last,
        actual_tokens=sum(attempt.actual_tokens for attempt in attempts),
        attempts=len(attempts),
    )


def spend_accuracy(repo_root: Path, sizing: SizingConfig) -> SpendAccuracy:
    """Hold every bead's forecast spend against what its dispatches really spent.

    The population is the dispatch ledger (:func:`run_record.dispatch_history`, so the
    committed markers count and a fresh clone measures the same thing), filtered to the
    write phases and to adapter-measured actuals — the same two rules
    :func:`unsized_lane_tokens` samples on, and for the same reasons: a rubric judge is
    not a lane, and a chars/4 estimate is not a measurement. The survivors are then
    folded per bead by :func:`_fold_lane`, because the forecast denominates a bead and
    not an attempt at one; :class:`SpendPair` carries why.

    A record that wrote its own spend forecast is compared against that number. An
    older one is compared against today's calibration applied to the working set it did
    record, via the one converter :func:`spend_from_working_set` — the alternative was a
    check that could not run until new records existed, and this engine has been burned
    before by a gate whose silence read as a pass.

    Read-only and best-effort, like every other reader of the ledger: an unreadable
    history yields an empty report, which :attr:`SpendAccuracy.pairs` makes visible.
    """
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
            if entry.get("outcome") == "failed":
                # What a dying attempt spent is not what the work costs.
                aborted += 1
                continue
            source = entry.get("forecast_source")
            if isinstance(source, str) and source.startswith("assumed:"):
                # A stand-in for a bead the estimator could not size is not a forecast.
                unscoped.append(bead_id)
                continue
            if entry.get("scope_tokens") == 0:
                # Same rule, reached by a different route (basicly-jr0l.69): a record
                # whose scope read to zero was forecast from pure overhead, so it is a
                # stand-in too — whatever `forecast_source` calls it. These predate the
                # `greenfield` absence and would otherwise be scored as prediction
                # skill: two of them missed by 20.3x and 11.8x, which is a fact about
                # the estimator having nothing to read, not about the lane.
                #
                # Not a widened band. The band is untouched and every record that had a
                # scope to forecast from is still held to it; this drops the ones that
                # never carried a prediction at all.
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
                    # No multiplier is declared or measured for this key, so no spend
                    # number exists to hold the lane to — the same indeterminate answer
                    # `SpendForecast` reports as None rather than as a zero.
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
    return SpendAccuracy(
        pairs=tuple(
            sorted(
                (_fold_lane(tuple(attempts)) for attempts in lanes.values()),
                key=lambda pair: (pair.timestamp, pair.bead),
            )
        ),
        unsized=unsized,
        incomparable=tuple(sorted(set(incomparable))),
        unmetered=unmetered,
        aborted=aborted,
        unscoped=tuple(sorted(set(unscoped))),
    )


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
            "build_factor_source": estimate.build_factor_source,
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

    The ``## Plan`` section records the three fields the plan gate added, so a lane
    dispatched later can be held to the plan it was decomposed under
    (:func:`plan_gate.build_entry_verdict`). Without it the fields would live only in
    the plan document, which nothing keeps once the children exist.
    """
    return policy.compose_body(
        spec.type,
        {
            "## Acceptance Criteria": "\n".join(f"- {item}" for item in spec.acceptance),
            plan_record.SCOPE_HEADING: "\n".join(f"- `{glob}`" for glob in spec.scope),
            # :func:`decompose` gates before it records, so these are never absent on
            # the real path. The fall-backs record an *empty* value rather than a
            # plausible one, so a spec that reached here ungated is refused again by
            # the entry predicate instead of looking declared.
            plan_record.PLAN_HEADING: plan_record.render_plan_section(
                spec.depends_on or (),
                spec.budget_tokens or 0,
                spec.integrity or "",
            ),
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
    # The declared paths that are load-bearing for that grouping, so every consumer
    # can name them without recomputing (basicly-jr0l.45).
    collapsing: tuple[CollapsingPath, ...] = ()

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
    """Create child issues under *feature_id* and wire the declared and computed graphs.

    The plan gate runs first and the sizing governor second, both before anything is
    recorded: a plan missing a field, or whose declared dependencies contain a cycle,
    is refused with no issue created, because a half-recorded decomposition is worse
    than none — the children exist, nothing owns un-creating them, and the next run
    creates a second set.

    Two sources of ``blocks`` edges, unioned:

    * **declared** — what the plan says must land first, resolved from sibling titles
      to the ids just created. This is ordering the scopes cannot express: B needing
      A's decision is invisible to a glob comparison when the two touch no common
      file, and before this existed the graph simply did not carry it.
    * **computed** — the serial chain within a scope-overlap group, which is a
      parallel-*safety* fact rather than an ordering one.

    Each child is created with acceptance criteria (so DoR passes), its recorded
    ``## Plan`` section, and the parent's labels (so it stays in the parent's phase).
    The resulting graph is checked for cycles before the result — carrying the parallel
    groups, the serial order, and the paths that were load-bearing for the grouping —
    is returned.
    """
    if not children:
        raise ValueError("decompose needs at least one child spec")

    plan_gate.require_plan(children)
    govern_working_set(repo_root, children, feature_id=feature_id)
    contended = append_only_paths(repo_root)
    groups = group_children(children, contended)
    predecessors = chain_predecessors(groups)

    inherited = feature_labels(repo_root, feature_id)
    issue_ids = [_create_child(repo_root, feature_id, spec, inherited) for spec in children]
    by_title = {spec.title: issue_ids[index] for index, spec in enumerate(children)}

    created: list[CreatedChild] = []
    for index, spec in enumerate(children):
        pred = predecessors[index]
        # Declared first so the graph reads in the plan's own order; the computed
        # chain then adds only what it did not already say. dict.fromkeys dedupes a
        # declared edge that the scope chain would have drawn anyway, without which
        # the same pair would be added twice.
        wanted = [by_title[dep] for dep in (spec.depends_on or ())]
        if pred is not None:
            wanted.append(issue_ids[pred])
        depends_on = tuple(dict.fromkeys(wanted))
        for dep_id in depends_on:
            _run_br(repo_root, ["dep", "add", issue_ids[index], dep_id, "-t", "blocks"])
        created.append(CreatedChild(issue_ids[index], spec, groups[index], depends_on))

    _assert_no_new_cycles(repo_root, set(issue_ids))

    grouped: dict[int, list[str]] = {}
    for child in created:
        grouped.setdefault(child.group, []).append(child.issue_id)
    group_tuples = tuple(tuple(grouped[g]) for g in sorted(grouped))

    return DecomposeResult(
        feature_id, tuple(created), group_tuples, collapsing_paths(children, contended)
    )


@dataclass(frozen=True)
class PlannedChild:
    """A child's computed placement before anything is recorded (for ``--dry-run``)."""

    spec: ChildSpec
    group: int
    predecessor: int | None


def preview(
    children: tuple[ChildSpec, ...], contended: tuple[str, ...] = ()
) -> tuple[PlannedChild, ...]:
    """Compute grouping and serial chains without touching ``br`` (pure).

    *contended* must be the same append-only list :func:`decompose` will load
    (:func:`append_only_paths`), or the preview is not a preview of the run — the
    grouping is exactly what the two have to agree on.
    """
    groups = group_children(children, contended)
    predecessors = chain_predecessors(groups)
    return tuple(
        PlannedChild(spec, groups[index], predecessors[index])
        for index, spec in enumerate(children)
    )

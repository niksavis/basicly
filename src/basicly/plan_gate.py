"""The plan gate: what a unit of work must declare before BUILD spends tokens on it.

Placed on **entry to BUILD**, never on its exit. Three independent arguments put the
inspection ahead of the expensive stage rather than after it: Shingo ranks inspection
placement source > self-check > successive; Theory of Constraints inspects *before*
the constraint so it never spends capacity on an already-defective item; and BUILD is
where nearly all of this engine's tokens measurably go. A plan defect found at verify
has already been paid for.

The gate refuses a plan unless every child carries five things:

* **acceptance** — at least one criterion, so VERIFY has something to derive a check
  from rather than a judgement to make;
* **scope** — at least one glob, so parallel-safety is computable and the landing
  scope check has something to hold the lane to;
* **depends_on** — a *declared* list, empty or not. Declared-empty and absent are
  different answers and only one of them is a plan: before this field existed the
  only ordering signal was scope overlap, which cannot express "B needs A's decision"
  between two children that touch no common file;
* **budget_tokens** — what this unit is worth spending, decided while it is still
  cheap to decide;
* **integrity** — which of the three levels (:data:`INTEGRITY_LEVELS`) selects the
  gate set, model tier and rework allowance for the unit.

A **proposed** plan owes one more, and only a proposed plan does: **demonstration**
(D18) — how the child is exercised end to end, naming a command, a request or a test
through the consumer surface. It is the checkable proxy for horizontal slicing. Our
decomposer groups by scope-glob overlap, which is file adjacency, so "add the model /
add the service / add the CLI" is the shape it cuts by construction; each of those has
no consumer-visible behaviour, so D10 has no check to derive from it and the defect is
only discovered at verify, with the tokens already spent. A child whose author cannot
name the demonstration is refused at plan time, when splitting is still cheap.

It is deliberately **not** in :data:`PLAN_FIELDS`, which is what
:func:`plan_entry.entry_verdict_for` holds a *recorded* bead to. Every child recorded
before this field existed carries a ``## Plan`` heading and no demonstration line, so on
that population absence is ambiguous between a defect and predating the rule — and a
predicate that cannot tell those apart refuses the whole tracker, which is a stopped
harness rather than a bound one. The plan gate has no such population: a proposed plan
is authored fresh every time.

Two whole-plan rules follow from the third: titles must be unique (a title-keyed graph
with a duplicate key names an edge nobody can resolve) and the declared graph must be
acyclic. A cycle is reported by naming its members, because "the plan has a cycle" is
not something an author can act on.

The gate is stated over a :class:`PlannedUnit` protocol rather than over
``decompose.ChildSpec`` so that it can sit *below* the decomposer in the import stack:
the module that enforces a rule must not depend on the one being enforced.

How a plan is *written down* and read back is :mod:`basicly.plan_record`'s, not this
module's. The boundary is recorded form against judgement: nothing there decides
whether a plan is adequate, and nothing here parses markup.

The same five fields read back off the tracker, for the units the decomposer never saw,
are :mod:`basicly.plan_entry`'s. That half was split out on the same boundary when this
module crossed the size cap: what a plan must declare is judgement, and reading a bead
back to see whether it did is a query against a different population.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

# The three integrity levels, in ascending blast radius: L1 routine (docs, comments,
# test-only), L2 internal (engine code behind no consumer surface), L3 consumer (the
# CLI, the `basicly.toml` schema, the catalog source schemas, the generated-file
# contract, the ledger format). The rule that *assigns* one deliberately lives
# elsewhere; the gate only insists that a level was assigned.
INTEGRITY_LEVELS = ("L1", "L2", "L3")

# The five per-child fields, in the order a violation report lists them. Named once so
# the plan schema, the gate message and the build-entry predicate cannot drift into
# describing different sets.
PLAN_FIELDS = ("acceptance", "scope", "depends_on", "budget_tokens", "integrity")

# The sixth field, required of a proposed plan only (see the module docstring for why
# the recorded population cannot be held to it).
DEMONSTRATION_FIELD = "demonstration"

# What makes a demonstration a demonstration rather than a claim: one backticked span
# naming the thing to run — the command, the request, the test node id. The same rule
# :func:`plan_record.backticked_entries` holds a scope glob to, and for the same
# reason: prose is not a runnable thing, and reading it as one lets a child look
# demonstrated when nothing demonstrates it. It is a floor, not a judgement — the gate
# asks whether the author could name one, which is the property horizontal slicing
# fails.
_NAMES_SOMETHING = re.compile(r"`[^`\n]+`")

# Depth-first colours for :func:`declared_cycles`: on the current path, and finished.
_OPEN = "open"
_DONE = "done"


@runtime_checkable
class PlannedFields(Protocol):
    """The five plan fields, wherever they are carried.

    Split from :class:`PlannedUnit` because the fields outlive the plan: a proposed
    child carries them beside its title, while :class:`RecordedPlan` carries the same
    five read back off a bead whose title is the bead's own. :func:`missing_fields`
    asks only about the five, so it takes this narrower shape.

    Every member is a read-only property rather than a mutable attribute. A plain
    ``title: str`` in a protocol declares a *writable* slot, which a frozen dataclass
    can never satisfy — so ``decompose.ChildSpec``, the one type this protocol exists
    to describe, failed to match it.
    """

    @property
    def acceptance(self) -> tuple[str, ...]:
        """The testable acceptance criteria this unit declares."""
        ...

    @property
    def scope(self) -> tuple[str, ...]:
        """The file globs this unit declares it touches."""
        ...

    @property
    def depends_on(self) -> tuple[str, ...] | None:
        """The siblings this unit follows; ``None`` is silence, ``()`` a declared answer."""
        ...

    @property
    def budget_tokens(self) -> int | None:
        """The token budget this unit is dispatched under."""
        ...

    @property
    def integrity(self) -> str | None:
        """The level selecting this unit's gate set, tier and rework allowance."""
        ...

    @property
    def demonstration(self) -> str | None:
        """How this unit is exercised end to end, through the consumer surface."""
        ...


@runtime_checkable
class PlannedUnit(PlannedFields, Protocol):
    """One proposed child: the five plan fields plus the title that keys the graph."""

    @property
    def title(self) -> str:
        """The proposed child's title, which the declared dependency graph is keyed on."""
        ...


# --- The plan gate over a proposed plan -------------------------------------


class PlanGateError(ValueError):
    """A plan the gate refused. A ``ValueError`` so plan-schema callers still catch it."""

    def __init__(self, verdict: PlanVerdict) -> None:
        """Carry the *verdict* alongside its rendered reason, so a caller can read it."""
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass(frozen=True)
class PlanVerdict:
    """Why the gate refused a plan, or that it did not (pure data)."""

    violations: tuple[str, ...] = ()
    # Each cycle as its member titles in traversal order, rotated to start at the
    # smallest so two runs over the same graph name it identically.
    cycles: tuple[tuple[str, ...], ...] = ()

    @property
    def refused(self) -> bool:
        """True when the plan may not proceed to BUILD."""
        return bool(self.violations or self.cycles)

    @property
    def reason(self) -> str:
        """Every reason the plan was refused, in one message an author can act on."""
        parts = list(self.violations)
        # The first member is repeated at the end so the loop is visible as a loop; a
        # cycle rendered open reads like an ordinary chain.
        parts += [
            "the declared dependency graph has a cycle through " + " -> ".join((*cycle, cycle[0]))
            for cycle in self.cycles
        ]
        return "; ".join(parts)


def missing_fields(unit: PlannedFields) -> tuple[str, ...]:
    """The names of *unit*'s absent plan fields, in :data:`PLAN_FIELDS` order."""
    present = {
        "acceptance": bool(unit.acceptance),
        "scope": bool(unit.scope),
        # Declared-empty passes; absent does not. `()` is an answer, `None` is silence.
        "depends_on": unit.depends_on is not None,
        "budget_tokens": unit.budget_tokens is not None,
        "integrity": bool(unit.integrity),
    }
    return tuple(field for field in PLAN_FIELDS if not present[field])


def demonstration_fault(unit: PlannedFields) -> str:
    """Why *unit*'s end-to-end demonstration does not count, or ``""`` when it does.

    Three answers, deliberately distinct, because the remedy differs. **Absent** is the
    horizontal slice D18 is aimed at, and the remedy is to re-cut the child rather than
    to write a sentence. **Naming nothing runnable** is a child that has one and said so
    in prose, and the remedy is to name the command. **Spanning lines** is neither: the
    recorded form is one line per field, so a multi-line value would be silently
    truncated to its first line by :func:`plan_record.parse_plan_section` and read back
    as something the author did not write.
    """
    text = (unit.demonstration or "").strip()
    if not text:
        return (
            f"declares no {DEMONSTRATION_FIELD}; a child that cannot name how it is "
            "exercised end to end has no consumer-visible behaviour for a check to be "
            "derived from, so split the plan differently rather than describing this one"
        )
    if "\n" in text:
        return (
            f"declares a {DEMONSTRATION_FIELD} spanning several lines; it is recorded as "
            "one line and would read back truncated, so state it in one"
        )
    if not _NAMES_SOMETHING.search(text):
        return (
            f"declares a {DEMONSTRATION_FIELD} naming nothing runnable ({text!r}); name "
            "the command to run, the request to make or the test that exercises it "
            "through the consumer surface, backticked, as a scope glob is"
        )
    return ""


def declared_cycles(units: tuple[PlannedUnit, ...]) -> tuple[tuple[str, ...], ...]:
    """Every cycle in the title-keyed graph the plan declares (deterministic, pure).

    Iterative depth-first search with the classic open/done colouring, walking the
    declared edges in declared order, so the answer is a function of the plan and
    nothing else. An edge naming a title the plan does not contain is not a cycle and
    is not this function's finding — it is a violation :func:`gate_plan` reports
    separately.
    """
    titles = {unit.title for unit in units}
    edges = {
        unit.title: tuple(dep for dep in (unit.depends_on or ()) if dep in titles) for unit in units
    }
    found: list[tuple[str, ...]] = []
    seen: set[tuple[str, ...]] = set()
    colour: dict[str, str] = {}

    for start, outgoing in edges.items():
        if start in colour:
            continue
        colour[start] = _OPEN
        path = [start]
        stack = [(start, iter(outgoing))]
        while stack:
            _, children = stack[-1]
            child = next(children, None)
            if child is None:
                stack.pop()
                colour[path.pop()] = _DONE
                continue
            if colour.get(child) == _OPEN:
                # `child` is still on the path, so the walk closed a loop: the members
                # are the path from `child` onwards.
                cycle = _canonical_cycle(tuple(path[path.index(child) :]))
                if cycle not in seen:
                    seen.add(cycle)
                    found.append(cycle)
                continue
            if colour.get(child) == _DONE:
                continue
            colour[child] = _OPEN
            path.append(child)
            stack.append((child, iter(edges.get(child, ()))))
    return tuple(found)


def _canonical_cycle(members: tuple[str, ...]) -> tuple[str, ...]:
    """*members* rotated to start at the smallest, so a cycle has one spelling."""
    pivot = members.index(min(members))
    return members[pivot:] + members[:pivot]


def gate_plan(units: tuple[PlannedUnit, ...]) -> PlanVerdict:
    """The gate's verdict on a whole plan (pure).

    Reports *every* reason at once rather than the first: an author who fixes one
    field per round trip pays a dispatch for each, and the round trips are the cost
    this gate exists to avoid.
    """
    violations: list[str] = []
    if not units:
        return PlanVerdict(("a plan must contain at least one unit of work",))

    titles = [unit.title for unit in units]
    duplicates = sorted({title for title in titles if titles.count(title) > 1})
    violations += [
        f"the title {title!r} is used by more than one child, so a declared dependency "
        "on it names no single child"
        for title in duplicates
    ]

    known = set(titles)
    for index, unit in enumerate(units):
        where = f"children[{index}] ({unit.title!r})"
        missing = missing_fields(unit)
        if missing:
            violations.append(
                f"{where} declares no {', '.join(missing)}; the plan gate refuses a unit "
                "BUILD cannot be held to"
            )
        fault = demonstration_fault(unit)
        if fault:
            violations.append(f"{where} {fault}")
        if unit.integrity and unit.integrity not in INTEGRITY_LEVELS:
            violations.append(
                f"{where} declares integrity {unit.integrity!r}, which is not one of "
                f"{list(INTEGRITY_LEVELS)}"
            )
        if unit.budget_tokens is not None and unit.budget_tokens <= 0:
            violations.append(
                f"{where} declares a budget of {unit.budget_tokens} tokens; a budget "
                "that cannot be spent is not a budget"
            )
        violations += [
            f"{where} declares a dependency on {dep!r}, which is not a child of this plan"
            for dep in (unit.depends_on or ())
            if dep not in known
        ]

    return PlanVerdict(tuple(violations), declared_cycles(units))


def require_plan(units: tuple[PlannedUnit, ...]) -> None:
    """Raise :class:`PlanGateError` when the gate refuses *units*; else return."""
    verdict = gate_plan(units)
    if verdict.refused:
        raise PlanGateError(verdict)

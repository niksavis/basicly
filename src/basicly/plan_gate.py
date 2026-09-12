from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Protocol, runtime_checkable

INTEGRITY_LEVELS = ("L1", "L2", "L3")

PLAN_FIELDS = ("acceptance", "scope", "depends_on", "budget_tokens", "integrity")

DEMONSTRATION_FIELD = "demonstration"

_NAMES_SOMETHING = re.compile(r"`[^`\n]+`")

_OPEN = "open"
_DONE = "done"


@runtime_checkable
class PlannedFields(Protocol):
    @property
    def acceptance(self) -> tuple[str, ...]: ...

    @property
    def scope(self) -> tuple[str, ...]: ...

    @property
    def depends_on(self) -> tuple[str, ...] | None: ...

    @property
    def budget_tokens(self) -> int | None: ...

    @property
    def integrity(self) -> str | None: ...

    @property
    def demonstration(self) -> str | None: ...


@runtime_checkable
class PlannedUnit(PlannedFields, Protocol):
    @property
    def title(self) -> str: ...


class PlanGateError(ValueError):
    def __init__(self, verdict: PlanVerdict) -> None:
        super().__init__(verdict.reason)
        self.verdict = verdict


@dataclass(frozen=True)
class PlanVerdict:
    violations: tuple[str, ...] = ()
    cycles: tuple[tuple[str, ...], ...] = ()

    @property
    def refused(self) -> bool:
        return bool(self.violations or self.cycles)

    @property
    def reason(self) -> str:
        parts = list(self.violations)
        parts += [
            "the declared dependency graph has a cycle through " + " -> ".join((*cycle, cycle[0]))
            for cycle in self.cycles
        ]
        return "; ".join(parts)


def missing_fields(unit: PlannedFields) -> tuple[str, ...]:
    present = {
        "acceptance": bool(unit.acceptance),
        "scope": bool(unit.scope),
        "depends_on": unit.depends_on is not None,
        "budget_tokens": unit.budget_tokens is not None,
        "integrity": bool(unit.integrity),
    }
    return tuple(field for field in PLAN_FIELDS if not present[field])


def demonstration_fault(unit: PlannedFields) -> str:

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
    pivot = members.index(min(members))
    return members[pivot:] + members[:pivot]


def gate_plan(units: tuple[PlannedUnit, ...]) -> PlanVerdict:

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
    verdict = gate_plan(units)
    if verdict.refused:
        raise PlanGateError(verdict)

"""Shared stand-ins for the plan gate, plan record and demonstration suites.

All three need the same things: a ``br`` that records what the decomposer tried to
create, and a child — or a recorded bead body — that *passes* the gate, so a test can
remove exactly one thing from it. Two of them held a verbatim copy each until the gate
grew its sixth field, at which point a copy that was not updated would have turned every
test in that file red for a reason having nothing to do with what it asserts. One
definition, so a field added to the gate is added once.

Deliberately plain module-level helpers rather than ``conftest`` fixtures: they are
constructors, not per-test state, and a test that builds two children wants to call
them twice.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import br, decompose, plan_record
from basicly.decompose import ChildSpec

if TYPE_CHECKING:
    import pytest

# What a child says about how it is exercised end to end (D18). Backticked, because the
# gate refuses a demonstration that names nothing runnable.
DEMONSTRATION = "run `basicly decompose feat --plan plan.toml --dry-run`"


class Proc:
    """A ``subprocess.CompletedProcess`` stand-in with only what the readers touch."""

    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        """Hold *stdout* and *returncode*; stderr is always empty."""
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class FakeBr:
    """Stateful stand-in for the br CLI, routed by subcommand.

    Hands out sequential child ids on create and records every dep-add edge, which is
    what the declared-graph and "creates no issue" assertions read. Deliberately raises
    on any call it was not taught, so a test cannot pass because the decomposer quietly
    stopped calling br.
    """

    def __init__(self, *, records: dict[str, dict] | None = None) -> None:
        """Answer ``show`` from *records*; start with nothing created."""
        self.records = records or {}
        self.created: list[tuple[str, str, str]] = []  # (id, title, body)
        self.edges: list[tuple[str, str, str]] = []  # (issue, depends_on, type)
        self._counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> Proc:
        """Serve one br invocation, or fail the test naming the unexpected call."""
        if args[:1] == ["create"]:
            self._counter += 1
            issue_id = f"feat.{self._counter}"
            self.created.append((issue_id, args[1], args[args.index("-d") + 1]))
            return Proc(json.dumps({"id": issue_id}))
        if args[:1] == ["show"]:
            record = self.records.get(args[1], {"id": args[1], "labels": []})
            return Proc(json.dumps([record]))
        if args[:2] == ["dep", "add"]:
            self.edges.append((args[2], args[3], args[args.index("-t") + 1]))
            return Proc("")
        if args[:2] == ["dep", "cycles"]:
            return Proc(json.dumps({"cycles": [], "count": 0}))
        if args[:2] == ["comments", "list"]:
            return Proc(json.dumps([]))
        if args[:2] == ["comments", "add"]:
            return Proc("")
        raise AssertionError(f"unexpected br call: {args}")


def install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., Proc]) -> None:
    """Route both br seams — the decomposer's and the record reader's — at *fake*."""
    monkeypatch.setattr(decompose, "_run_br", fake)
    monkeypatch.setattr(br, "try_run_br", fake)


def planned(title: str, *scope: str, **overrides: object) -> ChildSpec:
    """A child that passes the gate, so a test can remove exactly one thing."""
    fields: dict[str, object] = {
        "title": title,
        "acceptance": ("given a plan when it is gated then it passes",),
        "scope": scope or (f"src/{title}.py",),
        "depends_on": (),
        "budget_tokens": 40_000,
        "integrity": "L2",
        "demonstration": DEMONSTRATION,
    }
    fields.update(overrides)
    return ChildSpec(**fields)  # type: ignore[arg-type]


def plan_payload(*children: dict) -> dict:
    """A plan document wrapping *children*, as ``parse_children`` expects it."""
    return {"children": list(children)}


def child_payload(title: str, **overrides: object) -> dict:
    """The JSON form of :func:`planned`, for the plan-document entry parser."""
    payload: dict[str, object] = {
        "title": title,
        "acceptance": ["given a plan when it is gated then it passes"],
        "scope": [f"src/{title}.py"],
        "depends_on": [],
        "budget_tokens": 40_000,
        "integrity": "L2",
        "demonstration": DEMONSTRATION,
    }
    payload.update(overrides)
    return payload


def recorded_body(**overrides: object) -> str:
    """A bead body carrying every plan field, so a test can drop exactly one.

    Every field the *entry predicate* reads, which is why there is no demonstration
    line: this is the shape of a bead recorded before D18, and that it is still admitted
    is the assertion in ``test_plan_demonstration.py``. Adding one here would delete
    that test's subject.
    """
    fields: dict[str, object] = {
        "acceptance": ("given the lane when it is dispatched then it is held to this",),
        "scope": ("src/a.py",),
        "depends_on": (),
        "budget_tokens": 40_000,
        "integrity": "L2",
    }
    fields.update(overrides)
    sections = []
    if fields["acceptance"]:
        entries = "\n".join(f"- {item}" for item in fields["acceptance"])  # type: ignore[union-attr]
        sections.append(f"{plan_record.ACCEPTANCE_HEADING}\n\n{entries}")
    if fields["scope"]:
        entries = "\n".join(f"- `{glob}`" for glob in fields["scope"])  # type: ignore[union-attr]
        sections.append(f"{plan_record.SCOPE_HEADING}\n\n{entries}")
    plan_lines = []
    if fields["integrity"] is not None:
        plan_lines.append(f"- integrity: `{fields['integrity']}`")
    if fields["budget_tokens"] is not None:
        plan_lines.append(f"- budget: `{fields['budget_tokens']}`")
    if fields["depends_on"] is not None:
        declared = (
            ", ".join(f"`{dep}`" for dep in fields["depends_on"])  # type: ignore[union-attr]
            or plan_record.NOTHING_DECLARED
        )
        plan_lines.append(f"- depends on: {declared}")
    if plan_lines:
        sections.append(plan_record.PLAN_HEADING + "\n\n" + "\n".join(plan_lines))
    return "\n\n".join(sections) + "\n"

from __future__ import annotations

import json
import shutil
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

from basicly import plan_record
from basicly.decompose import ChildSpec
from tests import fake_tracker

if TYPE_CHECKING:
    import pytest

DEMONSTRATION = "run `basicly decompose feat --plan plan.toml --dry-run`"


class Proc:
    def __init__(self, stdout: str = "", returncode: int = 0) -> None:
        self.stdout = stdout
        self.stderr = ""
        self.returncode = returncode


class FakeBr:
    def __init__(self, *, records: dict[str, dict] | None = None) -> None:
        self.records = records or {}
        self.created: list[tuple[str, str, str]] = []
        self.typed: dict[str, dict[str, str]] = {}
        self.edges: list[tuple[str, str, str]] = []
        self._counter = 0

    def __call__(self, _repo_root: Path, args: list[str], *, _check: bool = True) -> Proc:
        if args[:1] == ["create"]:
            self._counter += 1
            issue_id = f"feat.{self._counter}"
            self.created.append((issue_id, args[1], args[args.index("-d") + 1]))
            self.typed[issue_id] = {
                name: args[args.index(flag) + 1]
                for flag, name in (
                    ("--acceptance", plan_record.ACCEPTANCE_FIELD),
                    ("--requirements", "requirements"),
                )
                if flag in args
            }
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

    def record(self, issue_id: str) -> dict[str, str]:
        body = next(created[2] for created in self.created if created[0] == issue_id)
        return {"description": body, **self.typed[issue_id]}


def install(monkeypatch: pytest.MonkeyPatch, fake: Callable[..., Proc]) -> None:
    fake_tracker.install(monkeypatch, fake)


def planned(title: str, *scope: str, **overrides: object) -> ChildSpec:
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
    return {"children": list(children)}


def child_payload(title: str, **overrides: object) -> dict:
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

    fields: dict[str, object] = {
        "acceptance": ("given the lane when it is dispatched then it is held to this",),
        "scope": ("src/a.py",),
        "depends_on": (),
        "budget_tokens": 40_000,
        "integrity": "L2",
    }
    fields.update(overrides)
    sections = []
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


def recorded(**overrides: object) -> dict[str, str]:

    acceptance = overrides.get(
        "acceptance", ("given the lane when it is dispatched then it is held to this",)
    )
    entries = "\n".join(f"- {item}" for item in acceptance)  # type: ignore[union-attr]
    return {"description": recorded_body(**overrides), plan_record.ACCEPTANCE_FIELD: entries}


def install_kit(root: Path) -> None:

    source = Path(__file__).resolve().parent.parent / ".basicly" / "core" / "kit" / "tracker"
    shutil.copytree(source, root / ".basicly" / "core" / "kit" / "tracker", dirs_exist_ok=True)

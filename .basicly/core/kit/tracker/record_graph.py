from __future__ import annotations

import importlib.util
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

_HERE = Path(__file__).resolve().parent
DEPTH = 8


def _load(file_name: str, module_name: str) -> Any:
    cached = sys.modules.get(module_name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(module_name, _HERE / file_name)
    if spec is None or spec.loader is None:
        raise ImportError("the tracker kit's " + file_name + " is missing from beside it")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


differential = _load("differential.py", "basicly_tracker_kit_differential")
VOCABULARY = differential.DEFAULT_VOCABULARY


def node(record: str, views: Mapping[str, Any], states: Mapping[str, Any]) -> dict[str, Any]:
    state = states.get(record)
    view = views.get(record)
    fields = state.fields if state is not None else {}
    return {
        "id": record,
        "title": str(fields.get("title") or ""),
        "status": "unknown" if view is None else view.status or "",
        "issue_type": str(fields.get("issue_type") or ""),
        "close_resolution": str(fields.get("close_resolution") or ""),
    }


def _parent(view: Any) -> str | None:
    return next(
        (edge.target for edge in view.dependencies if edge.type == VOCABULARY.parent_child_type),
        None,
    )


def _open(view: Any) -> bool:
    return not view.tombstoned and view.status not in VOCABULARY.closed_statuses


class Graph:
    def __init__(self, views: Mapping[str, Any], states: Mapping[str, Any]) -> None:
        self.views = views
        self.states = states
        self.children = differential.children_of(views, VOCABULARY)

    def ancestors(self, record: str) -> list[dict[str, Any]]:
        chain: list[dict[str, Any]] = []
        seen = {record}
        view = self.views.get(record)
        while view is not None and len(chain) < DEPTH:
            parent = _parent(view)
            if parent is None or parent in seen:
                break
            seen.add(parent)
            chain.append(node(parent, self.views, self.states))
            view = self.views.get(parent)
        return chain[::-1]

    def holding(self, record: str, children: bool = True) -> list[tuple[str, str]]:
        view = self.views.get(record)
        if view is None:
            return []
        found = [
            (edge.target, "blocks")
            for edge in view.dependencies
            if edge.type in VOCABULARY.blocking_types
            and differential.blocker_holds(self.views.get(edge.target), VOCABULARY)
        ]
        if children:
            found += [
                (child, "child")
                for child in self.children.get(record, ())
                if _open(self.views[child])
            ]
        return found

    def startable(self, record: str) -> bool:
        view = self.views.get(record)
        return view is not None and differential.is_ready(
            view, self.views, self.children, VOCABULARY
        )

    def waits(self, record: str) -> list[dict[str, Any]]:
        return self._waits(record, DEPTH, {record})

    def _waits(self, record: str, depth: int, expanded: set[str]) -> list[dict[str, Any]]:
        rows = []
        for target, via in self.holding(record):
            row = node(target, self.views, self.states)
            row.update(via=via, startable=self.startable(target), repeat=target in expanded)
            row["waits"] = []
            if not row["repeat"]:
                expanded.add(target)
                if depth > 1:
                    row["waits"] = self._waits(target, depth - 1, expanded)
            rows.append(row)
        return rows

    def parent_waits(self, record: str) -> dict[str, Any] | None:
        view = self.views.get(record)
        parent = _parent(view) if view is not None else None
        if parent is None:
            return None
        held = self.holding(parent, children=False)
        return {
            "id": parent,
            "waits": [node(target, self.views, self.states) for target, _ in held],
        }

    def shown(self, record: str) -> dict[str, object]:
        return {
            "ancestors": self.ancestors(record),
            "waits": self.waits(record),
            "parent_waits": self.parent_waits(record),
        }

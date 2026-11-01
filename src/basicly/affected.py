from __future__ import annotations

import os
import subprocess
import tempfile
from collections.abc import Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from typing import Any

from .hook_runtime import load as _module

_STATE: ContextVar[Any] = ContextVar("affected_snapshot", default=None)


def parse_provider(section: object, names: list[str]) -> dict[str, Any] | None:
    return _module("affected_checks").parse_provider(section, names)


def environment(root: Path) -> dict[str, str]:
    state = _STATE.get()
    return state.env if state is not None and state.tree == root else dict(os.environ)


def active(root: Path) -> bool:
    state = _STATE.get()
    return state is not None and state.tree == root


def select(
    root: Path,
    provider: dict[str, Any] | None,
    names: list[str],
    inputs: dict[str, tuple[str, ...]],
) -> dict[str, Any]:
    env = environment(root)
    if provider is None or len(set(names)) != len(names):
        return declared_selection(root, names, inputs, env)
    plan = _module("affected_checks").select(
        root, provider, names, snapshot_tree=env.get("BASICLY_SNAPSHOT_TREE"), env=env
    )
    plan["paths"] = sorted({
        change[key]
        for change in plan.get("request", {}).get("changes", [])
        for key in ("old_path", "path")
        if key in change
    })
    return plan


@contextmanager
def snapshot(root: Path) -> Iterator[Path]:
    if _STATE.get() is not None or os.environ.get("BASICLY_SNAPSHOT_TREE"):
        raise ValueError("already inside a staged snapshot; configure check commands directly")
    with tempfile.TemporaryDirectory(prefix="basicly-affected-") as directory:
        state = _module("staged-snapshot").prepare(root, Path(directory))
        token = _STATE.set(state)
        try:
            yield state.tree
        finally:
            _STATE.reset(token)


def parse_inputs(value: object, label: str) -> tuple[str, ...]:
    return _module("change_scope").parse_inputs(value, label)


def declared_selection(
    root: Path, names: list[str], inputs: dict[str, tuple[str, ...]], env: dict[str, str]
) -> dict[str, Any]:
    plan: dict[str, Any] = {
        "selected": names,
        "paths": [],
        "reasons": {},
        "fallback": "",
        "invalid_snapshot": False,
    }
    try:
        inventory = _module("affected_checks").changes(root, env)
        plan["paths"] = sorted({
            change[key] for change in inventory for key in ("old_path", "path") if key in change
        })
        if not inventory:
            plan.update(selected=[], reasons=dict.fromkeys(names, "no staged changes"))
            return plan
        request = _module("affected_checks")._request(
            root, names, env.get("BASICLY_SNAPSHOT_TREE"), env
        )
    except (OSError, ValueError, IndexError, subprocess.SubprocessError) as exc:
        plan["fallback"] = f"{exc}; declared check set retained"
        return plan
    paths = sorted({
        change[key]
        for change in request["changes"]
        for key in ("old_path", "path")
        if key in change
    })
    plan.update(paths=paths, snapshot_tree=request["snapshot_tree"], base_tree=request["base_tree"])
    config_changed = any(
        path in {"basicly.toml", "basicly.local.toml"} or path.startswith("basicly.d/")
        for path in paths
    )
    if len(set(names)) != len(names):
        plan["fallback"] = "duplicate check names; declared check set retained"
        return plan
    selected = []
    matcher = _module("check_reuse").matcher
    for name in names:
        patterns = inputs[name]
        if config_changed:
            reason = "verification configuration changed"
        elif not patterns:
            reason = "undeclared inputs; check retained"
        elif any(matcher(patterns).match(path) for path in paths):
            reason = "staged paths match declared inputs"
        else:
            plan["reasons"][name] = "staged paths do not match declared inputs"
            continue
        selected.append(name)
        plan["reasons"][name] = reason
    plan["selected"] = selected
    return plan

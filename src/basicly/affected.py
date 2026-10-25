from __future__ import annotations

import os
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


def select(root: Path, provider: dict[str, Any] | None, names: list[str]) -> dict[str, Any]:
    env = environment(root)
    if provider is None:
        return {
            "selected": names,
            "fallback": "no verify.affected provider configured; broad verification retained",
            "invalid_snapshot": False,
        }
    return _module("affected_checks").select(
        root, provider, names, snapshot_tree=env.get("BASICLY_SNAPSHOT_TREE"), env=env
    )


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

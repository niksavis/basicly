from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_share import (
    _ids,
    _machine,
    _share,
    _write,
    bare,
    git_runner,
    vault_queue,
    vault_share,
)

__all__ = ["bare"]


def test_a_single_share_includes_events_queued_during_its_first_build(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    first = _write(clone, state, 1, "initial")
    later: list[str] = []

    def arriving(args: list[str], cwd: Path, **options: Any) -> str:
        if "commit-tree" in args and not later:
            later.extend(_write(clone, state, 1, "arrived during the build"))
        return git_runner.run(args, cwd, **options)

    shown = _share(
        clone,
        state,
        runner=arriving,
        plan=vault_share.Plan(time.time, urgent=True, keep_going=False),
    )
    assert shown["pending"] == 0
    assert shown["shared"] == len(first + later) == 2
    assert set(_ids(bare)) == set(first + later)
    assert vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME) == []


def test_a_retry_includes_events_queued_during_a_refused_push(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    first = _write(clone, state, 1, "initial")
    later: list[str] = []

    def arriving(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args and not later:
            later.extend(_write(clone, state, 1, "arrived during the refused push"))
            raise git_runner.GitCommandError("push", 1, "non-fast-forward")
        return git_runner.run(args, cwd, **options)

    shown = _share(clone, state, runner=arriving)

    assert shown["pending"] == 0
    assert shown["shared"] == len(first + later) == 2
    assert set(_ids(bare)) == set(first + later)
    assert all(count == 1 for count in _ids(bare).values())


def test_a_retry_preserves_events_published_by_another_writer(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    other, other_state = _machine(tmp_path, bare, "two")
    first = _write(clone, state, 1, "initial")
    second = _write(other, other_state, 1, "another writer")
    raced: list[bool] = []

    def racing(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args and not raced:
            raced.append(True)
            _share(other, other_state)
            raise git_runner.GitCommandError("push", 1, "non-fast-forward")
        return git_runner.run(args, cwd, **options)

    shown = _share(clone, state, runner=racing)

    assert shown["pending"] == 0
    assert shown["shared"] == 1
    assert set(_ids(bare)) == set(first + second)
    assert all(count == 1 for count in _ids(bare).values())


@pytest.mark.parametrize("stage", ["hash-object", "commit-tree"])
def test_an_expired_build_never_pushes_or_loses_queued_events(
    tmp_path: Path, bare: Path, stage: str
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    written = _write(clone, state, 1, "initial")
    elapsed = [0.0]
    pushed: list[str] = []

    def slow(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            pushed.append(args[-1])
        result = git_runner.run(args, cwd, **options)
        if stage in args:
            elapsed[0] = 12.0
        return result

    with pytest.raises(vault_share.DeadlineError, match="deadline"):
        _share(
            clone,
            state,
            runner=slow,
            plan=vault_share.Plan(time.time, urgent=True, deadline_s=12),
            clock=lambda: elapsed[0],
        )

    assert pushed == []
    assert _ids(bare) == {}
    assert [
        line["id"] for line in vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)
    ] == written

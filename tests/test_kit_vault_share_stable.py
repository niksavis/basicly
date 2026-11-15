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


@pytest.mark.parametrize("urgent", [False, True])
def test_an_urgent_share_includes_arrivals_during_each_build(
    tmp_path: Path, bare: Path, urgent: bool
) -> None:
    clone, state = _machine(tmp_path, bare, "stable")
    written = _write(clone, state, 1, "initial")
    arrivals = 0
    pushed: list[str] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal arrivals
        result = git_runner.run(args, cwd, **options)
        if "commit-tree" in args and arrivals < 2:
            arrivals += 1
            written.extend(_write(clone, state, 1, f"arrival {arrivals}"))
        if "push" in args:
            pushed.append(args[-1])
        return result

    shown = _share(
        clone,
        state,
        runner=runner,
        plan=vault_share.Plan(time.time, urgent=urgent, keep_going=False),
    )

    assert len(pushed) == 1
    assert arrivals == 2
    assert shown["pending"] == (0 if urgent else 1)
    assert set(_ids(bare)) == set(written if urgent else written[:2])
    assert [event["id"] for event in vault_queue.pending(clone.folder / "queue.jsonl")] == (
        [] if urgent else written[2:]
    )


def test_a_terminal_decision_during_build_prevents_the_next_push(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "decided")
    written = _write(clone, state, 1, "keep queued")
    terminal = False
    pushed: list[str] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal terminal
        result = git_runner.run(args, cwd, **options)
        if "commit-tree" in args:
            terminal = True
        if "push" in args:
            pushed.append(args[-1])
        return result

    shown = _share(
        clone,
        state,
        runner=runner,
        plan=vault_share.Plan(time.time, urgent=True, stop_when=lambda: terminal),
    )

    assert terminal
    assert pushed == []
    assert shown["pending"] == 1
    assert _ids(bare) == {}
    assert [event["id"] for event in vault_queue.pending(clone.folder / "queue.jsonl")] == written


def test_continuous_arrivals_stop_at_the_existing_urgent_deadline(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "continuous")
    written = _write(clone, state, 1, "initial")
    elapsed = [0.0]
    pushed: list[str] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        result = git_runner.run(args, cwd, **options)
        if "commit-tree" in args:
            elapsed[0] += 0.4
            written.extend(_write(clone, state, 1, "arrived"))
        if "push" in args:
            pushed.append(args[-1])
        return result

    with pytest.raises(vault_share.DeadlineError):
        _share(
            clone,
            state,
            runner=runner,
            plan=vault_share.Plan(time.time, urgent=True, deadline_s=1),
            clock=lambda: elapsed[0],
        )

    assert pushed == []
    assert _ids(bare) == {}
    assert [event["id"] for event in vault_queue.pending(clone.folder / "queue.jsonl")] == written

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

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

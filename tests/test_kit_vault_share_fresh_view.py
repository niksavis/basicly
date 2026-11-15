from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tests.test_kit_vault_share import _ids, _machine, _share, _write, bare, git_runner, vault_share

__all__ = ["bare"]


def test_a_fresh_share_does_not_repeat_events_pushed_before_the_view_settles(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "fresh-view")
    vault_share.vault_clone.fetch(clone, clock=time.time)
    written = _write(clone, state, 1, "accepted once")
    published, release = threading.Event(), threading.Event()

    def paused(args: list[str], cwd: Path, **options: Any) -> str:
        result = git_runner.run(args, cwd, **options)
        if "push" in args:
            published.set()
            assert release.wait(5)
        return result

    with ThreadPoolExecutor(max_workers=1) as workers:
        first = workers.submit(_share, clone, state, runner=paused)
        try:
            assert published.wait(5)
            _share(clone, state, plan=vault_share.Plan(time.time, fresh=True, unlocked=True))
        finally:
            release.set()
        first.result(timeout=8)

    assert set(_ids(bare)) == set(written)
    assert all(count == 1 for count in _ids(bare).values())

from __future__ import annotations

import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Event
from typing import Any

from tests.test_kit_vault_unsettled import (
    ID,
    _ask,
    _claim,
    _cli,
    _machine,
    bare,
    clone_api,
    queue,
    vault_claim,
)

__all__ = ["bare"]


def test_cancelling_a_losing_attempt_fences_a_delayed_publisher_without_releasing_the_winner(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "loser")
    clone_api.fetch(clone, clock=time.time)
    attempt = _claim(clone, state)
    winner, winner_state = _machine(tmp_path, bare, "winner")
    assert vault_claim.claim(_ask(winner, winner_state, "s2"))["exit"] == 0
    built, resume = Event(), Event()
    first = True

    def delayed(args: list[str], *values: Any, **options: Any) -> str:
        nonlocal first
        if "push" in args and first:
            first = False
            built.set()
            assert resume.wait(10.0)
        return vault_claim.git_runner.run(args, *values, **options)

    with ThreadPoolExecutor(max_workers=1) as pool:
        publishing = pool.submit(
            vault_claim.vault_share.share,
            clone,
            state,
            plan=vault_claim.vault_share.Plan(time.time, urgent=True, unlocked=True),
            runner=delayed,
        )
        try:
            assert built.wait(10.0)
            cancelled = _cli(tmp_path, state, bare, "claim", ID, "--cancel")
            assert cancelled.returncode == 0, cancelled.stdout + cancelled.stderr
        finally:
            resume.set()
        publishing.result(timeout=15.0)

    clone_api.fetch(clone, clock=time.time)
    assert vault_claim.holder(clone, ID) == (queue.writer_of(winner_state), "s2")
    assert attempt["id"] not in clone_api.valid_ids(clone)

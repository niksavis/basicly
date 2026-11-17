from __future__ import annotations

import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, bare

__all__ = ["bare"]


def test_a_joined_loser_uses_the_known_winner_while_the_publisher_remains_busy(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    ready, send, published, finish = (threading.Event() for _ in range(4))

    def leading(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            ready.set()
            assert send.wait(5)
            result = vault_claim.git_runner.run(args, cwd, **options)
            published.set()
            assert finish.wait(5)
            return result
        return vault_claim.git_runner.run(args, cwd, **options)

    def follower(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            raise vault_claim.git_runner.GitTimeoutError("the follower cannot publish")
        return vault_claim.git_runner.run(args, cwd, **options)

    with ThreadPoolExecutor(max_workers=2) as workers:
        leader = workers.submit(
            vault_claim.claim, _ask(clone, state, "leader", runner=leading), deadline=8
        )
        try:
            assert ready.wait(5)
            loser = workers.submit(
                vault_claim.claim, _ask(clone, state, "follower", runner=follower), deadline=1
            )
            end = time.monotonic() + 5
            while not any(
                line.get("session") == "follower"
                for line in vault_claim.vault_queue.pending(
                    clone.folder / vault_claim.vault_queue.QUEUE_NAME
                )
            ):
                assert time.monotonic() < end
                time.sleep(0.01)
            send.set()
            assert published.wait(5)
            vault_claim.vault_clone.fetch(clone, clock=time.time)
            shown = loser.result(timeout=3)
            assert shown["exit"] == vault_claim.LOST, shown
            assert shown["holder"]["session"] == "leader"
            assert not leader.done()
        finally:
            send.set()
            finish.set()
        assert leader.result(timeout=10)["exit"] == vault_claim.LANDED


def test_a_claim_stops_retrying_its_share_after_fetching_another_winner(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "follower")
    other, other_state = _machine(tmp_path, bare, "winner")
    ready, finish = threading.Event(), threading.Event()
    paused = [0.0]

    def ticks() -> float:
        return time.monotonic() - paused[0]

    def refused(args: list[str], cwd: Path, **options: Any) -> str:
        if "push" in args:
            ready.set()
            blocked = time.monotonic()
            assert finish.wait(5)
            paused[0] += time.monotonic() - blocked
            raise vault_claim.git_runner.GitTimeoutError("the follower cannot publish")
        return vault_claim.git_runner.run(args, cwd, **options)

    with ThreadPoolExecutor(max_workers=1) as workers:
        follower = workers.submit(
            vault_claim.claim,
            _ask(clone, state, "follower", runner=refused),
            ticks=ticks,
            deadline=1,
        )
        try:
            assert ready.wait(5)
            won = vault_claim.claim(_ask(other, other_state, "winner"))
            assert won["exit"] == vault_claim.LANDED
            finish.set()
            shown = follower.result(timeout=3)
            assert shown["exit"] == vault_claim.LOST, shown
            assert shown["holder"]["session"] == "winner"
        finally:
            finish.set()

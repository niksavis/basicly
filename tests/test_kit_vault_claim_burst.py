from __future__ import annotations

import threading
import time
from collections import Counter
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_claim import _ask, _hold_share_lock, vault_claim
from tests.test_kit_vault_share import _machine, bare

__all__ = ["bare"]


def test_a_joined_claim_reserves_time_for_takeover_inside_a_short_deadline(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    holder = _hold_share_lock(clone)
    try:
        started = time.monotonic()
        shown = vault_claim.claim(_ask(clone, state, "joined", "burst-short"), deadline=2)
        spent = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait(10)

    assert shown["exit"] == 0, shown
    assert spent < 2
    assert shown["joined"] is True


@pytest.mark.parametrize("shared_target", [True, False])
def test_fifty_two_burst_claims_on_thirteen_clones_answer_before_thirty_seconds(
    tmp_path: Path, bare: Path, shared_target: bool
) -> None:
    machines = [_machine(tmp_path, bare, f"m{n:02d}") for n in range(13)]
    start = threading.Barrier(52)

    def latent(args: list[str], cwd: Path, **options: Any) -> str:
        delay = 1.5 if "push" in args else (0.5 if args[0] == "fetch" else 0.0)
        timeout = float(options["timeout"])
        if delay >= timeout:
            time.sleep(timeout)
            raise vault_claim.git_runner.GitTimeoutError("the injected latency used the deadline")
        time.sleep(delay)
        return vault_claim.git_runner.run(args, cwd, **{**options, "timeout": timeout - delay})

    def claim(n: int) -> tuple[dict[str, Any], float]:
        clone, state = machines[n % 13]
        start.wait(timeout=30)
        started = time.monotonic()
        record = f"burst-{n % 13 if shared_target else n}"
        ask = _ask(clone, state, f"s{n}", record, runner=latent)
        return vault_claim.claim(ask), time.monotonic() - started

    with ThreadPoolExecutor(max_workers=52) as workers:
        answers = list(workers.map(claim, range(52)))

    codes = Counter(shown["exit"] for shown, _ in answers)
    assert codes == (Counter({0: 13, 3: 39}) if shared_target else Counter({0: 52})), answers
    assert all(spent < 30 for _, spent in answers), answers
    for n in range(13 if shared_target else 52):
        clone, _ = machines[n % 13]
        held = vault_claim.holder(clone, f"burst-{n}")
        winners = [
            shown for shown, _ in answers if shown["record"] == f"burst-{n}" and shown["exit"] == 0
        ]
        assert held is not None and len(winners) == 1


def test_a_live_sharer_finishes_after_takeover_without_lost_or_duplicate_events(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    paused = threading.Event()
    takeover_pushed = threading.Event()
    first_push = True

    def slow(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal first_push
        if "push" in args and first_push:
            first_push = False
            paused.set()
            assert takeover_pushed.wait(timeout=8)
        return vault_claim.git_runner.run(args, cwd, **options)

    def takeover(args: list[str], cwd: Path, **options: Any) -> str:
        shown = vault_claim.git_runner.run(args, cwd, **options)
        if "push" in args:
            takeover_pushed.set()
        return shown

    with ThreadPoolExecutor(max_workers=1) as worker:
        leading = worker.submit(
            vault_claim.claim,
            _ask(clone, state, "leader", "burst-leader", runner=slow),
            deadline=10,
        )
        try:
            assert paused.wait(timeout=5)
            joined = vault_claim.claim(
                _ask(clone, state, "joined", "burst-joined", runner=takeover), deadline=2
            )
        finally:
            takeover_pushed.set()
        leader = leading.result(timeout=12)

    assert joined["exit"] == leader["exit"] == 0, (joined, leader)
    assert joined["joined"] is True
    shapes = vault_claim.vault_clone.vault_shapes
    verdicts = shapes.check(bare, "main")
    assert all(verdict.valid for verdict in verdicts)
    claims = [e for e in shapes.valid_events(verdicts) if e.get("kind") == "claim"]
    assert Counter(e["record"] for e in claims) == Counter({"burst-leader": 1, "burst-joined": 1})
    assert len({e["id"] for e in claims}) == 2
    assert vault_claim.vault_queue.pending(clone.folder / vault_claim.vault_queue.QUEUE_NAME) == []


def test_a_join_with_almost_no_budget_refuses_once_without_spinning(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    vault_claim.vault_clone.fetch(clone, clock=time.time)
    holder = _hold_share_lock(clone)
    calls: list[object] = []
    share = vault_claim.vault_share.share

    def counted(*args: Any, **options: Any) -> dict[str, object]:
        calls.append(options["plan"])
        return share(*args, **options)

    monkeypatch.setattr(vault_claim, "_check", lambda *_: None)
    monkeypatch.setattr(vault_claim.vault_share, "share", counted)
    try:
        started = time.monotonic()
        shown = vault_claim.claim(_ask(clone, state, "joined", "burst-floor"), deadline=0.01)
        spent = time.monotonic() - started
    finally:
        holder.kill()
        holder.wait(10)

    assert shown["exit"] == 4 and shown["unsettled"]
    assert len(calls) == 1
    assert spent < 1

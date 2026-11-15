from __future__ import annotations

import time
from collections import Counter
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, bare, git_runner

__all__ = ["bare"]


@pytest.mark.parametrize("winner", ["own", "foreign"])
def test_a_canonical_claim_outcome_stops_transport_before_the_next_retry(
    tmp_path: Path, bare: Path, winner: str
) -> None:
    clone, state = _machine(tmp_path, bare, "terminal")
    other, other_state = _machine(tmp_path, bare, "other")
    decided = False
    transports: list[str] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal decided
        if args[0] == "fetch":
            assert not decided, "a canonical outcome triggered another retry transport"
            transports.append("fetch")
        if "push" in args:
            if winner == "own":
                git_runner.run(args, cwd, **options)
                vault_claim.vault_clone.settle(clone)
            else:
                shown = vault_claim.claim(_ask(other, other_state, "winner"))
                assert shown["exit"] == vault_claim.LANDED
                vault_claim.vault_clone.fetch(clone, clock=time.time)
            decided = True
            raise git_runner.GitTimeoutError("the acknowledgement was withheld")
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(_ask(clone, state, "claimant", runner=runner))

    assert shown["exit"] == (vault_claim.LANDED if winner == "own" else vault_claim.LOST)
    assert transports == ["fetch"]
    shapes = vault_claim.vault_clone.vault_shapes
    verdicts = shapes.check(bare, "main")
    assert all(verdict.valid for verdict in verdicts)
    ids = Counter(
        event["id"] for event in shapes.valid_events(verdicts) if event.get("kind") == "claim"
    )
    assert len(ids) == 1
    assert list(ids.values()) == [1]

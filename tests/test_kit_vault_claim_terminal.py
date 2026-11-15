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


@pytest.mark.parametrize("winner", ["own", "foreign", "missing-view"])
def test_a_known_canonical_outcome_survives_an_expired_share(
    tmp_path: Path, bare: Path, winner: str
) -> None:
    clone, state = _machine(tmp_path, bare, "expired")
    other, other_state = _machine(tmp_path, bare, "other")
    record = "terminal-expired"
    expired = False
    transports: list[str] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal expired
        if args[0] == "fetch":
            assert not expired, "an expired share triggered another transport"
            transports.append("fetch")
        if "push" in args:
            if winner in {"own", "missing-view"}:
                git_runner.run(args, cwd, **options)
                if winner == "own":
                    vault_claim.vault_clone.settle(clone)
            else:
                shown = vault_claim.claim(_ask(other, other_state, "winner", record))
                assert shown["exit"] == vault_claim.LANDED
                vault_claim.vault_clone.fetch(clone, clock=time.time)
            expired = True
            raise vault_claim.vault_share.DeadlineError("the share deadline expired")
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(_ask(clone, state, "claimant", record, runner=runner))

    expected = {
        "own": vault_claim.LANDED,
        "foreign": vault_claim.LOST,
        "missing-view": vault_claim.UNSETTLED,
    }
    assert shown["exit"] == expected[winner], shown
    assert shown["record"] == record
    assert transports == ["fetch"]
    if winner == "missing-view":
        assert shown["unsettled"]
        assert vault_claim.holder(clone, record) is None
    shapes = vault_claim.vault_clone.vault_shapes
    verdicts = shapes.check(bare, "main")
    assert all(verdict.valid for verdict in verdicts)
    ids = Counter(
        event["id"] for event in shapes.valid_events(verdicts) if event.get("kind") == "claim"
    )
    assert len(ids) == 1
    assert list(ids.values()) == [1]
    if winner == "missing-view":
        assert shown["claim"] in ids

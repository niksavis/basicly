from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_claim import _ask, vault_claim
from tests.test_kit_vault_share import _machine, bare, git_runner

__all__ = ["bare"]


def test_every_command_uses_the_original_claim_time_remaining(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "deadline")
    elapsed = [0.0]
    observed: list[tuple[float, float]] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        observed.append((float(options["timeout"]), 4.0 - elapsed[0]))
        elapsed[0] += 0.01
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(
        _ask(clone, state, "claimant", runner=runner), deadline=4.0, ticks=lambda: elapsed[0]
    )

    assert shown["exit"] == vault_claim.LANDED, shown
    assert len(observed) > 1
    assert all(0 < timeout <= remaining for timeout, remaining in observed)


def test_an_expired_probe_starts_no_more_commands_or_claims(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "expired-probe")
    elapsed = [0.0]
    started_after_expiry: list[list[str]] = []

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        if elapsed[0] >= 4.0:
            started_after_expiry.append(args)
        result = git_runner.run(args, cwd, **options)
        if args[0] == "fetch":
            elapsed[0] = 4.0
        return result

    shown = vault_claim.claim(
        _ask(clone, state, "claimant", runner=runner), deadline=4.0, ticks=lambda: elapsed[0]
    )

    assert started_after_expiry == []
    assert shown["exit"] == vault_claim.UNREACHABLE, shown
    assert vault_claim.vault_queue.pending(clone.folder / "queue.jsonl") == []


@pytest.mark.parametrize("winner", ["own", "foreign", "missing-view"])
def test_transport_exhaustion_leaves_time_for_the_claim_response(
    tmp_path: Path, bare: Path, winner: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "completion")
    other, other_state = _machine(tmp_path, bare, "other-completion")
    elapsed = [0.0]
    expired = False
    original_share = vault_claim.vault_share.share

    def share(*args: Any, **options: Any) -> dict[str, object]:
        options["clock"] = lambda: elapsed[0]
        return original_share(*args, **options)

    monkeypatch.setattr(vault_claim.vault_share, "share", share)

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal expired
        assert not expired
        if "push" in args:
            if winner == "foreign":
                shown = vault_claim.claim(_ask(other, other_state, "winner", "completion"))
                assert shown["exit"] == vault_claim.LANDED
                vault_claim.vault_clone.fetch(clone, clock=time.time)
            else:
                git_runner.run(args, cwd, **options)
                if winner == "own":
                    vault_claim.vault_clone.settle(clone)
            elapsed[0] += float(options["timeout"]) + 0.02
            expired = True
            raise git_runner.GitTimeoutError("the transport exhausted its budget before cleanup")
        return git_runner.run(args, cwd, **options)

    shown = vault_claim.claim(
        _ask(clone, state, "claimant", "completion", runner=runner),
        deadline=4.0,
        ticks=lambda: elapsed[0],
    )

    assert expired
    assert (
        shown["exit"]
        == {
            "own": vault_claim.LANDED,
            "foreign": vault_claim.LOST,
            "missing-view": vault_claim.UNSETTLED,
        }[winner]
    ), shown
    assert elapsed[0] < 4.0
    assert float(shown["timing"]["claim_s"]) < 4.0

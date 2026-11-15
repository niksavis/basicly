from __future__ import annotations

from pathlib import Path
from typing import Any

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

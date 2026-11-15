from __future__ import annotations

import threading
import time
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_share import _ids, _machine, _share, _write, bare, git_runner, vault_share

__all__ = ["bare"]


@pytest.mark.parametrize(
    "witness", ["ready", "unchanged", "missing", "mismatch", "missing-head", "missing-seen"]
)
@pytest.mark.parametrize("repeat", [False, True])
def test_an_urgent_retry_reuses_only_a_newer_completed_local_view(
    tmp_path: Path, bare: Path, witness: str, repeat: bool
) -> None:
    clone, state = _machine(tmp_path, bare, "retry")
    other, other_state = _machine(tmp_path, bare, "peer")
    written = _write(clone, state, 1, "own")
    vault_share.vault_clone.fetch(clone, clock=time.time)
    old = git_runner.run(["rev-parse", vault_share.vault_clone.HEAD_REF], clone.bare).strip()
    rejected = False
    retry_fetches = 0
    peer_ids: list[str] = []
    peer_generation = ""
    pushes = 0

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal rejected, retry_fetches, peer_generation, pushes
        if args[0] == "fetch" and rejected:
            retry_fetches += 1
        if "push" in args:
            pushes += 1
            if pushes == 2 and repeat:
                raise git_runner.GitCommandError("push", 1, "fixture transport failure")
        if "push" in args and not rejected:
            peer_ids.extend(_write(other, other_state, 1, "peer"))
            _share(other, other_state)
            if witness != "unchanged":
                vault_share.vault_clone.fetch(clone, clock=time.time)
            if witness == "missing":
                (clone.folder / vault_share.vault_clone.COVERS_NAME).unlink()
            if witness == "mismatch":
                git_runner.run(["update-ref", vault_share.vault_clone.SEEN_REF, old], clone.bare)
            if witness == "missing-head":
                git_runner.run(["update-ref", "-d", vault_share.vault_clone.HEAD_REF], clone.bare)
            if witness == "missing-seen":
                git_runner.run(["update-ref", "-d", vault_share.vault_clone.SEEN_REF], clone.bare)
            peer_generation = (
                clone.folder / vault_share.vault_clone.FETCH_GENERATION_NAME
            ).read_text()
            rejected = True
            raise git_runner.GitCommandError("push", 1, "non-fast-forward")
        return git_runner.run(args, cwd, **options)

    shown = vault_share.share(
        clone, state, plan=vault_share.Plan(time.time, urgent=True), runner=runner
    )

    assert rejected
    assert retry_fetches == (0 if witness == "ready" else 1) + int(repeat)
    assert shown["pending"] == 0
    assert _ids(bare) == dict.fromkeys([*written, *peer_ids], 1)
    current = (clone.folder / vault_share.vault_clone.FETCH_GENERATION_NAME).read_text()
    assert (current == peer_generation) is (witness == "ready" and not repeat)


def test_a_busy_completed_view_keeps_the_existing_independent_fetch_path(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, state = _machine(tmp_path, bare, "busy-retry")
    other, other_state = _machine(tmp_path, bare, "busy-peer")
    written = _write(clone, state, 1, "own")
    peer_ids = _write(other, other_state, 1, "peer")
    entered, release = threading.Event(), threading.Event()
    original_lock = vault_share.events.LedgerLock
    rejected = False
    retry_fetches = 0

    def hold() -> None:
        with original_lock(clone.folder / "fetch"):
            entered.set()
            assert release.wait(timeout=5)

    worker = threading.Thread(target=hold)

    @contextmanager
    def lock(directory: Path | str, **options: Any) -> Iterator[Any]:
        if (
            rejected
            and Path(directory) == clone.folder / "fetch"
            and options.get("timeout_s", 1) > 0
        ):
            release.set()
        with original_lock(directory, **options) as held:
            yield held

    def runner(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal rejected, retry_fetches
        if args[0] == "fetch" and rejected:
            retry_fetches += 1
        if "push" in args and not rejected:
            _share(other, other_state)
            vault_share.vault_clone.fetch(clone, clock=time.time)
            worker.start()
            assert entered.wait(timeout=5)
            rejected = True
            raise git_runner.GitCommandError("push", 1, "non-fast-forward")
        return git_runner.run(args, cwd, **options)

    monkeypatch.setattr(vault_share.events, "LedgerLock", lock)
    try:
        shown = vault_share.share(
            clone, state, plan=vault_share.Plan(time.time, urgent=True), runner=runner
        )
    finally:
        release.set()
        if worker.ident is not None:
            worker.join(timeout=5)
            assert not worker.is_alive()

    assert rejected and retry_fetches == 1
    assert shown["pending"] == 0
    assert _ids(bare) == dict.fromkeys([*written, *peer_ids], 1)

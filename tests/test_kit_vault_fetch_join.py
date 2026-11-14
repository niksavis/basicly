from __future__ import annotations

import threading
import time
from collections import Counter
from collections.abc import Iterator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from tests.test_kit_vault_clone import _batch, _clone, _fetch, git_runner, origin, vault_clone

if TYPE_CHECKING:
    from tests.test_kit_vault_shapes import Vault

__all__ = ["origin"]


def _joining_fetch(clone: Any, **options: Any) -> dict[str, Any]:
    options.setdefault("clock", time.time)
    return vault_clone.fetch_with(clone, vault_clone.Fetch(coalesce=True, **options))


@pytest.mark.parametrize(
    "first_succeeds,valid_marker", [(True, True), (False, True), (True, False)]
)
def test_overlapping_fetches_reuse_only_a_successful_completed_generation(
    tmp_path: Path,
    origin: Vault,
    monkeypatch: pytest.MonkeyPatch,
    first_succeeds: bool,
    valid_marker: bool,
) -> None:
    clone = _clone(tmp_path, origin)
    _fetch(clone, clock=lambda: 1000.0)
    expected = _batch(origin)
    entered, second_waiting, release = threading.Event(), threading.Event(), threading.Event()
    counts: Counter[str] = Counter()
    guard = threading.Lock()
    base_lock = vault_clone.events.LedgerLock
    attempts = 0

    @contextmanager
    def observed_lock(directory: Path | str, **kwargs: Any) -> Iterator[Any]:
        nonlocal attempts
        ordinal = 0
        if Path(directory) == clone.folder / "fetch":
            with guard:
                attempts += 1
                ordinal = attempts
                if attempts == 2:
                    second_waiting.set()
        with base_lock(directory, **kwargs) as held:
            yield held
            if ordinal == 1 and not valid_marker:
                (clone.folder / vault_clone.FETCH_GENERATION_NAME).write_text("invalid nonce")

    def runner(name: str):
        def run(args: list[str], cwd: Path, **options: Any) -> str:
            if args[0] == "fetch":
                counts[name] += 1
                if name == "first":
                    entered.set()
                    assert release.wait(5)
                    if not first_succeeds:
                        raise git_runner.GitTimeoutError("the first fixture fetch failed")
            return git_runner.run(args, cwd, **options)

        return run

    monkeypatch.setattr(vault_clone.events, "LedgerLock", observed_lock)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(_fetch, clone, clock=lambda: 1000.0, runner=runner("first"))
        try:
            assert entered.wait(5)
            second = workers.submit(
                _joining_fetch, clone, clock=lambda: 1000.0, runner=runner("second")
            )
            assert second_waiting.wait(5)
        finally:
            release.set()
        one, two = first.result(timeout=10), second.result(timeout=10)
    assert two["fetched"] is True and two["head"] == expected
    assert one["fetched"] is first_succeeds
    joined = first_succeeds and valid_marker
    assert two.get("coalesced", False) is joined
    assert counts == (
        Counter(first=1) if joined else Counter(first=1 if first_succeeds else 2, second=1)
    )


def test_a_later_fetch_does_not_reuse_an_earlier_success(tmp_path: Path, origin: Vault) -> None:
    clone = _clone(tmp_path, origin)
    previous = _fetch(clone, clock=lambda: 1000.0)
    _batch(origin)

    def unavailable(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            raise git_runner.GitTimeoutError("the later fixture fetch failed")
        return git_runner.run(args, cwd, **options)

    shown = _joining_fetch(clone, clock=lambda: 1000.0, runner=unavailable)
    assert shown["fetched"] is False
    assert shown["head"] == previous["head"]


def test_a_transport_success_without_completed_publication_cannot_be_reused(
    tmp_path: Path, origin: Vault, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone = _clone(tmp_path, origin)
    _fetch(clone, clock=lambda: 1000.0)
    expected = _batch(origin)
    generation = clone.folder / vault_clone.FETCH_GENERATION_NAME
    original = generation.read_bytes()
    entered, second_waiting, release = threading.Event(), threading.Event(), threading.Event()
    counts: Counter[str] = Counter()
    base_lock = vault_clone.events.LedgerLock
    entries = 0

    @contextmanager
    def observed_lock(directory: Path | str, **kwargs: Any) -> Iterator[Any]:
        nonlocal entries
        if Path(directory) == clone.folder / "fetch":
            entries += 1
            if entries == 2:
                second_waiting.set()
        with base_lock(directory, **kwargs) as held:
            yield held

    def runner(name: str):
        def run(args: list[str], cwd: Path, **options: Any) -> str:
            if args[0] == "fetch":
                counts[name] += 1
                if name == "first":
                    entered.set()
                    assert release.wait(5)
                else:
                    assert generation.read_bytes() == original
            if name == "first" and args[:2] == ["update-ref", vault_clone.SEEN_REF]:
                raise git_runner.GitTimeoutError("the fetched history could not be published")
            return git_runner.run(args, cwd, **options)

        return run

    monkeypatch.setattr(vault_clone.events, "LedgerLock", observed_lock)
    with ThreadPoolExecutor(max_workers=2) as workers:
        first = workers.submit(_fetch, clone, runner=runner("first"))
        try:
            assert entered.wait(5)
            second = workers.submit(_joining_fetch, clone, runner=runner("second"))
            assert second_waiting.wait(5)
        finally:
            release.set()
        with pytest.raises(git_runner.GitTimeoutError):
            first.result(timeout=10)
        shown = second.result(timeout=10)
    assert shown["fetched"] is True and shown["head"] == expected
    assert not shown.get("coalesced", False)
    assert counts == Counter(first=1, second=1)

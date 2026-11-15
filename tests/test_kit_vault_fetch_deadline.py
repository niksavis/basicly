from __future__ import annotations

import time
from pathlib import Path
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any

from tests.test_kit_vault_share import _machine, bare, git_runner, vault_share

if TYPE_CHECKING:
    import pytest

__all__ = ["bare"]


def test_a_fetch_retry_receives_only_the_remaining_transport_budget(
    tmp_path: Path, bare: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    clone, _state = _machine(tmp_path, bare, "fetch-deadline")
    model = vault_share.vault_clone
    previous = model.fetch(clone, clock=time.time)
    elapsed = [0.0]
    timeouts: list[float] = []
    monkeypatch.setattr(model, "time", SimpleNamespace(monotonic=lambda: elapsed[0]))

    def timed_out(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            timeout = float(options["timeout"])
            timeouts.append(timeout)
            elapsed[0] += timeout
            raise git_runner.GitTimeoutError("the transport consumed its budget")
        return git_runner.run(args, cwd, **options)

    shown = model.fetch(clone, clock=time.time, runner=timed_out, timeout=0.25)

    assert shown["fetched"] is False
    assert shown["head"] == previous["head"]
    assert timeouts == [0.25]

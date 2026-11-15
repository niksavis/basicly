from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_share import _ids, _machine, _share, _write, bare, git_runner, vault_share

__all__ = ["bare"]


def test_an_urgent_share_does_not_restart_transport_with_an_exhausted_budget(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "fetch-deadline")
    vault_share.vault_clone.fetch(clone, clock=time.time)
    written = _write(clone, state, 1, "keep pending")
    elapsed = [0.0]
    timeouts: list[float] = []

    def timed_out(args: list[str], cwd: Path, **options: Any) -> str:
        if args[0] == "fetch":
            timeout = float(options["timeout"])
            timeouts.append(timeout)
            elapsed[0] += timeout
            raise git_runner.GitTimeoutError("the transport consumed its budget")
        return git_runner.run(args, cwd, **options)

    with pytest.raises(vault_share.DeadlineError):
        _share(
            clone,
            state,
            runner=timed_out,
            plan=vault_share.Plan(time.time, urgent=True, deadline_s=0.25),
            clock=lambda: elapsed[0],
        )

    assert timeouts == [0.25]
    assert [
        row["id"] for row in vault_share.vault_queue.pending(clone.folder / "queue.jsonl")
    ] == written
    assert _ids(bare) == {}

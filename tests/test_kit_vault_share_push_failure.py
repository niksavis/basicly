from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_share import (
    _machine,
    _no_ci,
    _refusing,
    _share,
    _write,
    bare,
    git_runner,
    vault_queue,
    vault_share,
)

__all__ = ["_no_ci", "bare"]


def _timing_out(args: list[str], cwd: Path, **options: Any) -> str:
    if "push" in args:
        raise git_runner.GitTimeoutError("the push passed its timeout")
    return git_runner.run(args, cwd, **options)


def test_a_timed_out_share_push_says_it_timed_out_and_keeps_the_queue(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 2, "a")

    with pytest.raises(vault_share.ShareError) as refused:
        _share(clone, state, runner=_timing_out)

    assert "the share push timed out on attempt 5" in str(refused.value)
    assert "refused" not in str(refused.value)
    assert len(vault_queue.pending(clone.folder / vault_queue.QUEUE_NAME)) == 2


def test_a_rejected_share_push_names_git_s_reason(tmp_path: Path, bare: Path) -> None:
    clone, state = _machine(tmp_path, bare, "m1")
    _write(clone, state, 1, "a")
    pushes: list[int] = []

    with pytest.raises(vault_share.ShareError) as refused:
        _share(clone, state, runner=_refusing(pushes))

    assert "the vault refused the share 5 times; git said: rejected;" in str(refused.value)
    assert "timed out" not in str(refused.value)

from __future__ import annotations

import time
from pathlib import Path
from typing import Any

import pytest

from tests.test_kit_vault_share import _ids, _machine, _share, _write, bare, git_runner, vault_share

__all__ = ["bare"]


@pytest.mark.parametrize("tamper", ["remove", "replace"])
def test_claim_shares_restore_or_refuse_a_hook_changed_during_fetch(
    tmp_path: Path, bare: Path, tamper: str
) -> None:
    clone, state = _machine(tmp_path, bare, "tamper")
    vault_share.vault_clone.fetch(clone, clock=time.time)
    written = _write(clone, state, 1, "protected publication")
    before = git_runner.run(["rev-parse", "HEAD"], bare)
    changed = False

    def changing(args: list[str], cwd: Path, **options: Any) -> str:
        nonlocal changed
        result = git_runner.run(args, cwd, **options)
        if args[0] == "fetch" and not changed:
            changed = True
            hook = clone.bare / "hooks/pre-push"
            if tamper == "remove":
                hook.rename(hook.with_name("pre-push.saved"))
            else:
                hook.write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
        return result

    plan = vault_share.Plan(time.time, stop_when=lambda: False)
    if tamper == "replace":
        with pytest.raises(ValueError, match="unrelated pre-push"):
            _share(clone, state, plan=plan, runner=changing)
        assert git_runner.run(["rev-parse", "HEAD"], bare) == before
        assert [
            row["id"] for row in vault_share.vault_queue.pending(clone.folder / "queue.jsonl")
        ] == written
    else:
        _share(clone, state, plan=plan, runner=changing)
        assert "BASICLY_VAULT_PUSH_GUARD=1" in (clone.bare / "hooks/pre-push").read_text("utf-8")
        assert set(_ids(bare)) == set(written)
        assert all(count == 1 for count in _ids(bare).values())

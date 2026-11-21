from __future__ import annotations

from pathlib import Path
from typing import cast

from basicly import release
from tests.test_kit_vault_commit_hook import _check, _message, hook
from tests.test_kit_vault_share import _machine, bare

__all__ = ["bare"]


class _Plan:
    tag = "v9.9.9"
    date = "2026-11-21"
    current_tag = "v9.9.8"


def test_the_release_commit_message_passes_the_vault_commit_msg_hook(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "m")
    text = release.commit_message(cast("release.ReleasePlan", _Plan()), "acme-a1b2")

    shown = _check(clone, state, _message(tmp_path, text))

    assert text.splitlines()[0].endswith(" (acme-a1b2)")
    assert (shown["exit"], shown["record"]) == (hook.ACCEPTED, "acme-a1b2"), shown

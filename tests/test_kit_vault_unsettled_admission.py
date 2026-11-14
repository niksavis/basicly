from __future__ import annotations

import time
from pathlib import Path

import pytest

from tests.test_kit_vault_unsettled import (
    ID,
    _claim,
    _cli,
    _machine,
    bare,
    clone_api,
    queue,
    vault_claim,
)

__all__ = ["bare"]


@pytest.mark.parametrize(
    "bad", ["{broken", '{"kind":"claim","at":NaN}', '{"kind":"claim","at":true}']
)
def test_cancel_preserves_a_malformed_queue_and_names_the_repair(
    tmp_path: Path, bare: Path, bad: str
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    path = clone.folder / queue.QUEUE_NAME
    original = path.read_bytes() + (bad + "\n").encode()
    path.write_bytes(original)

    result = _cli(tmp_path, state, bare, "claim", ID, "--cancel")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fsck --repair" in result.stdout
    assert path.read_bytes() == original


def test_cancel_preserves_a_malformed_release_intent_instead_of_publishing_it(
    tmp_path: Path, bare: Path
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    queue.write(
        clone,
        [{"kind": "unassign", "record": ID, "payload": {"cancelled_claim": []}}],
        state=state,
        origin=queue.Origin("s1", False, time.time),
    )
    path = clone.folder / queue.QUEUE_NAME
    original = path.read_bytes()
    before = clone_api.valid_ids(clone)

    result = _cli(tmp_path, state, bare, "claim", ID, "--cancel")

    assert result.returncode == 1, result.stdout + result.stderr
    assert "fsck --repair" in result.stdout
    assert path.read_bytes() == original
    assert clone_api.valid_ids(clone) == before


@pytest.mark.parametrize("args", [("status",), ("sync", "--urgent"), ("hook", "prompt")])
def test_every_retry_path_preserves_and_names_a_malformed_release_intent(
    tmp_path: Path, bare: Path, args: tuple[str, ...]
) -> None:
    clone, state = _machine(tmp_path, bare, "one")
    clone_api.fetch(clone, clock=time.time)
    _claim(clone, state)
    queue.write(
        clone,
        [{"kind": "unassign", "record": ID, "payload": {"cancelled_claim": []}}],
        state=state,
        origin=queue.Origin("s1", False, time.time),
    )
    path = clone.folder / queue.QUEUE_NAME
    original = path.read_bytes()
    before = vault_claim.git_runner.run(["rev-parse", "refs/heads/main"], bare).strip()

    result = _cli(tmp_path, state, bare, *args)

    assert result.returncode == (0 if args[0] == "hook" else 1), result.stdout + result.stderr
    assert "fsck --repair" in result.stdout + result.stderr
    assert path.read_bytes() == original
    assert vault_claim.git_runner.run(["rev-parse", "refs/heads/main"], bare).strip() == before

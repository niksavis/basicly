from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from basicly import tracker
from tests.test_kit_vault_cli_wiring import LEDGER, _environment
from tests.test_kit_vault_share import bare
from tests.test_kit_vault_view import _carry, _push, _record
from tests.test_vault_harness_hooks import _frozen

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["_environment", "bare"]


def _pointer(tmp_path: Path, bare: Path) -> Path:
    root = _frozen(tmp_path, bare)
    _push(tmp_path, bare, "publisher", _carry(_record("acme-c3d4")))
    assert (root / LEDGER).is_file()
    return root


def test_a_landing_artifact_reaches_the_vault_when_the_ledger_is_a_pointer(
    tmp_path: Path, bare: Path
) -> None:
    root = _pointer(tmp_path, bare)
    tracker.add_artifact(root, "acme-c3d4", "landing-verification", {"passed": True})
    assert tracker.read_artifacts(root, "acme-c3d4") == {"landing-verification": {"passed": True}}
    assert (root / LEDGER).is_file()


def test_a_dispatch_spend_reaches_the_vault_when_the_ledger_is_a_pointer(
    tmp_path: Path, bare: Path
) -> None:
    root = _pointer(tmp_path, bare)
    tracker.add_dispatch(root, "acme-c3d4", {tracker.DISPATCH_SPEND_KEY: 7})
    kit = tracker.kit(root)
    found = kit.read_ledger(tracker.present_ledger(root))
    assert [
        e.payload[tracker.DISPATCH_SPEND_KEY] for e in found if e.kind == kit.events.KIND_DISPATCH
    ] == [7]


def test_an_artifact_for_a_record_the_vault_lacks_is_refused(tmp_path: Path, bare: Path) -> None:
    root = _pointer(tmp_path, bare)
    with pytest.raises(tracker.TrackerDivergenceError, match="acme-zzzz"):
        tracker.add_artifact(root, "acme-zzzz", "landing-verification", {"passed": True})

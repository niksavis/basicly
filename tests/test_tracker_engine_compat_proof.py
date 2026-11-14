from __future__ import annotations

import importlib
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import pytest


def test_actual_engine_write_reports_and_events_match_the_released_engine(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    scripts = Path(__file__).resolve().parents[1] / ".scripts"
    monkeypatch.syspath_prepend(str(scripts))
    proof = importlib.import_module("tracker_engine_compat")

    report = proof.compare("v0.45.0", tmp_path / "engine-proof")

    assert report["result"] == "pass"
    assert report["forms"] == 6

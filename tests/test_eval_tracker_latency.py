from __future__ import annotations

import hashlib
import importlib.util
from pathlib import Path
from typing import Any

import pytest

from basicly import tracker_query

ROOT = Path(__file__).resolve().parents[1]


def evaluator() -> Any:
    source = ROOT / ".scripts/eval_tracker_latency.py"
    spec = importlib.util.spec_from_file_location("eval_tracker_latency", source)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_tracker_latency_measures_real_http_without_writing_the_source(tmp_path: Path) -> None:
    server = tracker_query.board_kit(ROOT)
    cli = server.tracker_cli()
    ledger = tmp_path / "source"
    assert cli.main(["create", str(ledger), "--prefix", "demo", "--title", "seed"]) == 0
    (ledger / "snapshot.jsonl").write_text("invalid derived cache")
    before = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in ledger.iterdir() if p.is_file()
    }
    result = evaluator().measure(ROOT, ledger, 3)
    after = {
        p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in ledger.iterdir() if p.is_file()
    }
    assert after == before
    assert result["records"] == 1
    assert result["freshness"]["version_changed"] is True
    assert result["freshness"]["record_visible"] is True
    assert result["warm"]["stats"]["samples"] == 3
    assert result["warm"]["record"]["samples"] == 3
    assert result["budgets"]["status"] == "proposed"


@pytest.mark.parametrize("samples", [0, -1, 1001])
def test_tracker_latency_refuses_invalid_sample_counts_before_copying(
    tmp_path: Path, samples: int
) -> None:
    with pytest.raises(ValueError, match="samples"):
        evaluator().measure(ROOT, tmp_path / "absent-ledger", samples)

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

REPO_ROOT = Path(__file__).resolve().parents[1]


def _load() -> ModuleType:
    spec = importlib.util.spec_from_file_location(
        "claim_load", REPO_ROOT / ".scripts" / "claim_load.py"
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["claim_load"] = module
    spec.loader.exec_module(module)
    return module


claim_load = _load()


def _report(**overrides: object) -> dict[str, object]:
    shown: dict[str, object] = {
        "exits": {0: 10, 3: 2},
        "p99_s": 4.0,
        "max_s": 6.0,
        "records_with_two_winners": [],
        "sessions_without_claims": 0,
    }
    return {**shown, **overrides}


def test_the_gate_passes_a_report_inside_every_d1_limit() -> None:
    assert claim_load.gate(_report()) == []


@pytest.mark.parametrize(
    ("override", "reason"),
    [
        ({"exits": {0: 10, 4: 1}}, "did not end 0 or 3"),
        ({"p99_s": 10.5}, "p99"),
        ({"max_s": 61.0}, "above 60.0 s"),
        ({"records_with_two_winners": ["load-00001"]}, "two holders"),
        ({"sessions_without_claims": 3}, "made no claim"),
    ],
)
def test_the_gate_names_each_broken_limit(override: dict[str, object], reason: str) -> None:
    failed = claim_load.gate(_report(**override))

    assert any(reason in line for line in failed)


def test_a_run_with_foreign_cpu_above_the_limit_is_not_counted() -> None:
    noisy = _report(cpu_foreign={"mean": claim_load.FOREIGN_CPU_LIMIT + 0.01})
    quiet = _report(cpu_foreign={"mean": claim_load.FOREIGN_CPU_LIMIT})

    refused = claim_load.counted(noisy)
    assert refused is not None
    assert "not counted" in refused
    assert str(claim_load.FOREIGN_CPU_LIMIT) in refused
    assert claim_load.counted(quiet) is None


def test_a_small_real_load_makes_claims_in_every_session_and_one_holder_per_record(
    tmp_path: Path,
) -> None:
    config = claim_load.Config(sessions=4, machines=2, scale=8, minutes=8, seed=3)

    shown = claim_load.run_load(config, tmp_path)

    assert shown["sessions_without_claims"] == 0
    assert shown["records_with_two_winners"] == []
    assert set(shown["exits"]) <= {0, 3}
    assert shown["claims"] >= 4

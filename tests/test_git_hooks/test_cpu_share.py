from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]


def _hook(name: str):
    path = ROOT / ".basicly" / "core" / "hooks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"cpu_share_test_{name}", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


share = _hook("cpu_share")


@pytest.mark.parametrize(
    ("available", "jobs"), [(1, 1), (4, 1), (7, 1), (8, 1), (16, 2), (32, 4), (64, 8)]
)
def test_auto_jobs_grow_with_the_cores_and_never_fall_below_one(available: int, jobs: int) -> None:
    assert share.resolve_jobs("auto", available) == jobs


@pytest.mark.parametrize(("jobs", "available", "workers"), [(1, 4, 4), (4, 32, 8), (8, 4, 1)])
def test_workers_split_the_cores_between_the_jobs(jobs: int, available: int, workers: int) -> None:
    assert share.workers(jobs, available) == workers


def test_an_explicit_whole_number_is_kept() -> None:
    assert share.resolve_jobs(3, 64) == 3


@pytest.mark.parametrize("value", [0, -1, True, "2", 1.5, "AUTO", None])
def test_a_value_that_is_neither_auto_nor_a_positive_whole_number_is_refused(
    value: object,
) -> None:
    with pytest.raises(
        ValueError, match=r'verify\.jobs must be a whole number of at least 1 or "auto"'
    ):
        share.resolve_jobs(value, 8)


def test_only_the_exact_placeholder_argument_is_replaced() -> None:
    command = ["pytest", "-n", "{workers}", "-k", "x{workers}"]

    assert share.expand(command, 2, 16) == ["pytest", "-n", "8", "-k", "x{workers}"]


def test_the_git_hook_runner_fills_the_placeholder_for_its_job_count(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    printer = [sys.executable, "-c", "import sys; print('workers=' + sys.argv[1])", "{workers}"]
    (tmp_path / "basicly.toml").write_text(
        f'[verify]\njobs = 2\n[[verify.checks]]\nname = "printer"\n'
        f'command = {json.dumps(printer)}\nmodes = ["fast"]\n',
        encoding="utf-8",
    )

    assert _hook("check_runner").run_checks(tmp_path, "fast") == 0

    assert f"workers={share.workers(2)}" in capsys.readouterr().out

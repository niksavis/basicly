from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

from tests import pytest_shard

REPO_ROOT = Path(__file__).resolve().parent.parent

SAMPLE = """
import pytest


@pytest.mark.parametrize("case", range(40))
def test_case(case):
    assert case >= 0


def test_heavy():
    assert True
"""


def _ids(weights: dict[str, float], extra: int) -> list[str]:
    return [*weights, *(f"tests/test_unknown.py::test_{index}" for index in range(extra))]


@pytest.mark.parametrize("total", [1, 2, 3, 8, 10])
def test_the_shards_together_are_the_whole_suite_and_never_overlap(total: int) -> None:
    weights = pytest_shard.load_weights()
    placed = pytest_shard.plan(weights, total)
    nodeids = _ids(weights, 500)
    shards = [
        {nodeid for nodeid in nodeids if pytest_shard.shard_of(nodeid, placed, total) == index}
        for index in range(total)
    ]
    assert set().union(*shards) == set(nodeids)
    assert sum(len(shard) for shard in shards) == len(nodeids)


def test_a_tests_shard_depends_only_on_its_id_the_weights_and_the_count() -> None:
    weights = pytest_shard.load_weights()
    placed = pytest_shard.plan(weights, 8)
    nodeids = _ids(weights, 50)
    alone = {nodeid: pytest_shard.shard_of(nodeid, placed, 8) for nodeid in nodeids}
    again = pytest_shard.plan(dict(reversed(list(weights.items()))), 8)
    assert alone == {nodeid: pytest_shard.shard_of(nodeid, again, 8) for nodeid in nodeids}


def test_the_heaviest_shard_carries_at_most_the_mean_plus_the_heaviest_test() -> None:
    weights = pytest_shard.load_weights()
    placed = pytest_shard.plan(weights, 8)
    loads = [0.0] * 8
    for nodeid, shard in placed.items():
        loads[shard] += weights[nodeid]
    assert max(loads) <= sum(loads) / 8 + max(weights.values())


@pytest.mark.parametrize("value", ["0/4", "5/4", "2", "a/b", "2/0"])
def test_a_malformed_shard_is_refused_by_name(value: str) -> None:
    with pytest.raises(pytest.UsageError, match="K/N"):
        pytest_shard.parse(value)


def _collect(root: Path, *args: str) -> set[str]:
    env = {**os.environ, "PYTHONPATH": str(REPO_ROOT)}
    done = subprocess.run(
        [
            sys.executable,
            "-m",
            "pytest",
            "-p",
            "tests.pytest_shard",
            "-p",
            "no:cacheprovider",
            "--collect-only",
            "-q",
            "--rootdir",
            str(root),
            str(root),
            *args,
        ],
        capture_output=True,
        text=True,
        check=False,
        env=env,
        cwd=root,
    )
    assert done.returncode == 0, done.stdout + done.stderr
    return {line for line in done.stdout.splitlines() if "::" in line}


def test_pytest_runs_each_collected_test_in_exactly_one_shard(tmp_path: Path) -> None:
    (tmp_path / "test_sample.py").write_text(SAMPLE, encoding="utf-8")
    whole = _collect(tmp_path)
    shards = [_collect(tmp_path, "--shard", f"{index}/3") for index in (1, 2, 3)]
    assert len(whole) == 41
    assert set().union(*shards) == whole
    assert sum(len(shard) for shard in shards) == len(whole)

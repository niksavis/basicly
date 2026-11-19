from __future__ import annotations

import re
from pathlib import Path

import pytest

from basicly import pytest_inputs, verify_shard
from basicly.config import VerifyCheck


def _check(name: str, *command: str, reuse: str | None = None) -> VerifyCheck:
    return VerifyCheck(name=name, command=command, modes=frozenset({"full"}), reuse=reuse)


SUITE = _check("pytest", "pytest", "-q", "-n", "{workers}", reuse="pytest")

CHECKS = (
    _check("ruff", "ruff", "check"),
    _check("pyright-linux", "pyright", "--pythonplatform", "Linux"),
    SUITE,
    _check("pyright-darwin", "pyright", "--pythonplatform", "Darwin"),
    _check("bandit", "bandit", "-r", "src"),
    _check("pytest-fresh-runtime", "pytest", "-q", "-m", "fresh_runtime"),
    _check("mermaid", "uv", "run", "python", ".scripts/check_mermaid.py"),
)


@pytest.mark.parametrize("total", [1, 2, 3, 5, 8])
def test_every_check_other_than_the_suite_runs_whole_in_exactly_one_shard(total: int) -> None:
    shards = [verify_shard.select(CHECKS, index, total) for index in range(1, total + 1)]
    others = [check for shard in shards for check in shard if check.name != "pytest"]
    assert sorted(check.name for check in others) == sorted(
        check.name for check in CHECKS if check.name != "pytest"
    )
    assert all(verify_shard.OPTION not in check.command for check in others)


def test_every_shard_runs_its_own_part_of_the_suite() -> None:
    shards = [verify_shard.select(CHECKS, index, 3) for index in (1, 2, 3)]
    suites = [check.command for shard in shards for check in shard if check.name == "pytest"]
    assert suites == [(*SUITE.command, "--shard", f"{index}/3") for index in (1, 2, 3)]


def test_a_shard_of_the_suite_is_never_reused_as_the_full_suite_proof() -> None:
    (sharded,) = (c for c in verify_shard.select(CHECKS, 1, 8) if c.name == "pytest")
    assert not pytest_inputs.supported_command(sharded.command, "full")


WORKFLOWS = Path(__file__).resolve().parent.parent / ".github" / "workflows"


@pytest.mark.parametrize("name", ["quality-gates.yml", "release.yml"])
def test_a_sharded_workflow_runs_every_shard_its_command_names(name: str) -> None:
    text = (WORKFLOWS / name).read_text(encoding="utf-8")
    (listed,) = re.findall(r"shard: \[([\d, ]+)\]", text)
    totals = set(re.findall(r"--shard \$\{\{ matrix\.shard \}\}/(\d+)", text))
    assert len(totals) == 1
    (total,) = totals
    assert [int(part) for part in listed.split(",")] == list(range(1, int(total) + 1))


@pytest.mark.parametrize("value", ["0/4", "5/4", "4", "x/2"])
def test_a_malformed_shard_names_the_correct_command(value: str) -> None:
    with pytest.raises(ValueError, match="basicly verify --mode full --ci --shard 2/8"):
        verify_shard.parse(value)

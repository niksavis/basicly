from __future__ import annotations

import pytest

from basicly import verify_shard
from basicly.config import VerifyCheck


def _check(name: str, *command: str) -> VerifyCheck:
    return VerifyCheck(name=name, command=command, modes=frozenset({"full"}))


CHECKS = (
    _check("ruff", "ruff", "check"),
    _check("pyright-linux", "pyright", "--pythonplatform", "Linux"),
    _check("pytest", "pytest", "-q", "-n", "{workers}"),
    _check("pyright-darwin", "pyright", "--pythonplatform", "Darwin"),
    _check("bandit", "bandit", "-r", "src"),
    _check("pytest-fresh-runtime", "pytest", "-q", "-m", "fresh_runtime"),
    _check("mermaid", "uv", "run", "python", ".scripts/check_mermaid.py"),
)


@pytest.mark.parametrize("total", [1, 2, 3, 5, 8])
def test_every_check_other_than_pytest_runs_in_exactly_one_shard(total: int) -> None:
    shards = [verify_shard.select(CHECKS, index, total) for index in range(1, total + 1)]
    others = [check.name for shard in shards for check in shard if check.command[0] != "pytest"]
    assert sorted(others) == sorted(c.name for c in CHECKS if c.command[0] != "pytest")


def test_every_shard_runs_its_own_part_of_each_pytest_check() -> None:
    shard = verify_shard.select(CHECKS, 2, 3)
    pytest_commands = [check.command for check in shard if check.command[0] == "pytest"]
    assert pytest_commands == [
        ("pytest", "-q", "-n", "{workers}", "--shard", "2/3"),
        ("pytest", "-q", "-m", "fresh_runtime", "--shard", "2/3"),
    ]


@pytest.mark.parametrize("value", ["0/4", "5/4", "4", "x/2"])
def test_a_malformed_shard_names_the_correct_command(value: str) -> None:
    with pytest.raises(ValueError, match="basicly verify --mode full --ci --shard 2/8"):
        verify_shard.parse(value)

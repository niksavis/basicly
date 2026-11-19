from __future__ import annotations

import hashlib
from pathlib import Path

import pytest

from basicly import verify_shard

WEIGHTS_FILE = Path(__file__).resolve().parent / "shard_weights.json"
OPTION = verify_shard.OPTION


def parse(value: str) -> tuple[int, int]:
    try:
        return verify_shard.parse(value)
    except ValueError as exc:
        raise pytest.UsageError(f"{exc}; the form is {verify_shard.FORM}") from exc


def load_weights(path: Path = WEIGHTS_FILE) -> dict[str, float]:
    return verify_shard.suite_weights(verify_shard.load_weights(path))


def plan(weights: dict[str, float], total: int) -> dict[str, int]:
    return verify_shard.plan(weights, total)[0]


def shard_of(nodeid: str, placed: dict[str, int], total: int) -> int:
    if nodeid in placed:
        return placed[nodeid]
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(
        OPTION, default=None, help=f"run one shard of the suite, as {verify_shard.FORM}"
    )


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = config.getoption(OPTION)
    if value is None:
        return
    index, total = parse(value)
    weights = load_weights()
    placed = plan(weights, total)
    kept = [item for item in items if shard_of(item.nodeid, placed, total) == index - 1]
    dropped = [item for item in items if shard_of(item.nodeid, placed, total) != index - 1]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept

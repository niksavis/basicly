from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

WEIGHTS_FILE = Path(__file__).resolve().parent / "shard_weights.json"
OPTION = "--shard"
FORM = "K/N, with 1 <= K <= N"


def parse(value: str) -> tuple[int, int]:
    index, _, total = value.partition("/")
    if not (index.isdigit() and total.isdigit()) or not 1 <= int(index) <= int(total):
        raise pytest.UsageError(f"{OPTION} {value!r} is not {FORM}; for example {OPTION} 2/8")
    return int(index), int(total)


def load_weights(path: Path = WEIGHTS_FILE) -> dict[str, float]:
    return {str(key): float(value) for key, value in json.loads(path.read_text("utf-8")).items()}


def plan(weights: dict[str, float], total: int) -> dict[str, int]:
    loads = [0.0] * total
    placed: dict[str, int] = {}
    for nodeid, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(total), key=lambda shard: (loads[shard], shard))
        loads[target] += weight
        placed[nodeid] = target
    return placed


def shard_of(nodeid: str, placed: dict[str, int], total: int) -> int:
    if nodeid in placed:
        return placed[nodeid]
    digest = hashlib.sha256(nodeid.encode("utf-8")).digest()
    return int.from_bytes(digest[:8], "big") % total


def pytest_addoption(parser: pytest.Parser) -> None:
    parser.addoption(OPTION, default=None, help=f"run one shard of the suite, as {FORM}")


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    value = config.getoption(OPTION)
    if value is None:
        return
    index, total = parse(value)
    placed = plan(load_weights(), total)
    kept = [item for item in items if shard_of(item.nodeid, placed, total) == index - 1]
    dropped = [item for item in items if shard_of(item.nodeid, placed, total) != index - 1]
    if dropped:
        config.hook.pytest_deselected(items=dropped)
    items[:] = kept

from __future__ import annotations

import json
import os
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from basicly.config import VerifyCheck

SUITE = "pytest"
OPTION = "--shard"
FORM = "K/N, with 1 <= K <= N"
WEIGHTS = Path("tests") / "shard_weights.json"
CHECK_PREFIX = "check:"
JUNIT_DIR = Path("build") / "shard-junit"
UNKNOWN_CHECK_S = 1.0


def parse(value: str) -> tuple[int, int]:
    index, _, total = value.partition("/")
    if not (index.isdigit() and total.isdigit()) or not 1 <= int(index) <= int(total):
        raise ValueError(
            f"{OPTION} {value!r} is not {FORM}; run basicly verify --mode full --ci {OPTION} 2/8"
        )
    return int(index), int(total)


def load_weights(path: Path) -> dict[str, float]:
    if not path.is_file():
        return {}
    return {str(key): float(value) for key, value in json.loads(path.read_text("utf-8")).items()}


def suite_weights(weights: dict[str, float]) -> dict[str, float]:
    return {key: value for key, value in weights.items() if not key.startswith(CHECK_PREFIX)}


def plan(weights: dict[str, float], total: int) -> tuple[dict[str, int], list[float]]:
    loads = [0.0] * total
    placed: dict[str, int] = {}
    for nodeid, weight in sorted(weights.items(), key=lambda item: (-item[1], item[0])):
        target = min(range(total), key=lambda shard: (loads[shard], shard))
        loads[target] += weight
        placed[nodeid] = target
    return placed, loads


def split(check: VerifyCheck) -> bool:
    return check.reuse == SUITE and check.command[:1] == (SUITE,)


def place_checks(
    checks: tuple[VerifyCheck, ...], total: int, weights: dict[str, float], cores: int
) -> dict[str, int]:
    _, loads = plan(suite_weights(weights), total)
    wall = [load / max(1, cores) for load in loads]
    placed: dict[str, int] = {}
    others = [check for check in checks if not split(check)]
    costs = {c.name: weights.get(CHECK_PREFIX + c.name, UNKNOWN_CHECK_S) for c in others}
    for position, check in sorted(
        enumerate(others), key=lambda item: (-costs[item[1].name], item[0])
    ):
        target = min(range(total), key=lambda shard: (wall[shard], (shard - position) % total))
        wall[target] += costs[check.name]
        placed[check.name] = target
    return placed


def _suite_options(index: int, total: int) -> tuple[str, ...]:
    report = (JUNIT_DIR / f"{index}-of-{total}.xml").as_posix()
    return (
        OPTION,
        f"{index}/{total}",
        "--junitxml",
        report,
        "-o",
        "junit_family=xunit1",
    )


def select(
    checks: tuple[VerifyCheck, ...],
    index: int,
    total: int,
    weights: dict[str, float] | None = None,
    cores: int | None = None,
) -> tuple[VerifyCheck, ...]:
    placed = place_checks(checks, total, weights or {}, cores or os.process_cpu_count() or 1)
    return tuple(
        replace(check, command=(*check.command, *_suite_options(index, total)))
        if split(check)
        else check
        for check in checks
        if split(check) or placed[check.name] == index - 1
    )

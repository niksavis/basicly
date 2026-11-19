from __future__ import annotations

from dataclasses import replace
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from basicly.config import VerifyCheck

SUITE = "pytest"
OPTION = "--shard"
FORM = "K/N, with 1 <= K <= N"


def parse(value: str) -> tuple[int, int]:
    index, _, total = value.partition("/")
    if not (index.isdigit() and total.isdigit()) or not 1 <= int(index) <= int(total):
        raise ValueError(
            f"{OPTION} {value!r} is not {FORM}; run basicly verify --mode full --ci {OPTION} 2/8"
        )
    return int(index), int(total)


def split(check: VerifyCheck) -> bool:
    return check.reuse == SUITE and check.command[:1] == (SUITE,)


def select(checks: tuple[VerifyCheck, ...], index: int, total: int) -> tuple[VerifyCheck, ...]:
    others = [check for check in checks if not split(check)]
    chosen = {check.name for position, check in enumerate(others) if position % total == index - 1}
    return tuple(
        replace(check, command=(*check.command, OPTION, f"{index}/{total}"))
        if split(check)
        else check
        for check in checks
        if split(check) or check.name in chosen
    )

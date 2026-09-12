from __future__ import annotations

from pathlib import Path

from . import usage
from .config import load_verify_config

CAPABILITY_VERIFY_CHECK = "verify check"


def shipped_capabilities(repo_root: Path) -> tuple[tuple[str, str], ...]:

    return tuple(
        (
            f"{CAPABILITY_VERIFY_CHECK} {check.name!r}",
            f"{usage.VERIFY_CHECK_PREFIX}{check.name}",
        )
        for check in load_verify_config(repo_root).checks
    )


def recorded_executions(repo_root: Path) -> dict[str, int] | None:

    counters = usage.load_usage(repo_root)
    check_runs = usage.load_verify_checks(repo_root)
    if counters is None and check_runs is None:
        return None
    counts: dict[str, int] = {}
    for name, entry in (counters or {}).items():
        if executions := _counted(entry):
            counts[str(name)] = executions
    for name, entry in (check_runs or {}).items():
        if executions := _counted(entry):
            counts[f"{usage.VERIFY_CHECK_PREFIX}{name}"] = executions
    return counts


def _counted(entry: object) -> int:
    if not isinstance(entry, dict):
        return 0
    count = entry.get("count")
    if isinstance(count, int) and not isinstance(count, bool) and count > 0:
        return count
    return 0


def unexercised_capabilities(repo_root: Path) -> tuple[str, ...]:

    capabilities = shipped_capabilities(repo_root)
    if not capabilities:
        return ()
    counts = recorded_executions(repo_root)
    if not counts:
        return (
            f"no execution ledger at {usage.VERIFY_CHECKS_FILE.as_posix()} or "
            f"{usage.USAGE_FILE.as_posix()}, so "
            f"every declared capability is unproven ({len(capabilities)} declared): "
            "exercise them on this machine and re-run (`basicly verify` records every "
            "check it runs; `basicly usage report` shows what is recorded)",
        )
    return tuple(
        f"unexercised capability: {label} — nothing has recorded an execution under "
        f"{witness!r}; exercise it or drop the claim before tagging (`basicly verify` "
        "records a check it runs and watches pass, in each mode that declares it)"
        for label, witness in capabilities
        if counts.get(witness, 0) <= 0
    )

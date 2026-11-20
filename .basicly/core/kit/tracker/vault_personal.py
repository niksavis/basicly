from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

LEDGER_FIELD = "ledger"

Scan = Callable[[Sequence[Mapping[str, object]]], dict[str, int]]


def _counts(found: dict[str, int]) -> str:
    return ", ".join(f"{label} {count}" for label, count in sorted(found.items()) if count)


def in_import(scan: Scan | None, lines: Sequence[Mapping[str, object]]) -> str | None:
    counts = _counts(scan(lines)) if scan is not None else ""
    if not counts:
        return None
    return (
        f"the import still carries personal data ({counts}); nothing was published. "
        "Add --redact-name for each remaining form and preview again"
    )


def in_vault_copy(
    scan: Scan | None, held: Sequence[Mapping[str, Any]], found: Sequence[Any]
) -> str | None:
    if scan is None:
        return None
    wanted = {event.id for event in found}
    copy = [
        line
        for line in held
        if isinstance(line.get(LEDGER_FIELD), dict) and line[LEDGER_FIELD].get("id") in wanted
    ]
    counts = _counts(scan(copy))
    if not counts:
        return None
    return (
        f"the vault already carries this source, but its copy holds personal data ({counts}); "
        "the vault copy must be rewritten before the vault is made public"
    )

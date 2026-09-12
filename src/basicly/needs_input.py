from __future__ import annotations

import contextlib
import json
from dataclasses import dataclass
from pathlib import Path

SENTINEL_FILE = Path(".basicly/usage/needs-input.json")


@dataclass(frozen=True)
class NeedsInput:
    fact: str
    detail: str = ""


def take(cwd: Path) -> NeedsInput | None:

    path = cwd / SENTINEL_FILE
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError:
        return None
    with contextlib.suppress(OSError):
        path.unlink()
    try:
        data = json.loads(raw)
    except ValueError, TypeError:
        return None
    if not isinstance(data, dict):
        return None
    fact = data.get("fact")
    if not isinstance(fact, str) or not fact.strip():
        return None
    detail = data.get("detail")
    return NeedsInput(fact.strip(), detail.strip() if isinstance(detail, str) else "")

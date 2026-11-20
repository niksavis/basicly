from __future__ import annotations

from pathlib import Path

LEDGER = Path(".basicly") / "ledger"
ARCHIVE = Path(".basicly") / "ledger-archive"


def committed_ledger(root: Path) -> Path:
    ledger = root / LEDGER
    return root / ARCHIVE if ledger.is_file() else ledger

from __future__ import annotations

from pathlib import Path

LEDGER_DIR_NAME = Path(".basicly") / "ledger"
REDIRECT_NAME = "redirect"


def tracker_root(repo_root: Path) -> Path:

    root = Path(repo_root)
    redirect = root / LEDGER_DIR_NAME / REDIRECT_NAME
    if not redirect.is_file():
        return root
    try:
        named = redirect.read_text(encoding="utf-8").strip()
    except OSError:
        return root
    if not named:
        return root
    target = Path(named)
    return target if target.is_dir() else root


def ledger_dir(repo_root: Path) -> Path:
    return tracker_root(repo_root) / LEDGER_DIR_NAME

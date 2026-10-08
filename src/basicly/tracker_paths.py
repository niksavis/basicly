from __future__ import annotations

import importlib.util
from pathlib import Path
from types import ModuleType

from .catalog import bundled_catalog_root


def _redirect_rule() -> ModuleType:
    source = bundled_catalog_root() / "kit" / "tracker" / "git_layout.py"
    spec = importlib.util.spec_from_file_location("basicly_engine_git_layout", source)
    if spec is None or spec.loader is None:
        raise ImportError(f"the bundled ledger redirect rule cannot load from {source}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


_rule = _redirect_rule()

LEDGER_DIR_NAME = Path(".basicly") / "ledger"
REDIRECT_NAME: str = _rule.REDIRECT_FILE
RedirectError: type[ValueError] = _rule.RedirectError


def tracker_root(repo_root: Path) -> Path:

    root = Path(repo_root)
    return _rule.redirect_target(root / LEDGER_DIR_NAME, root) or root


def ledger_dir(repo_root: Path) -> Path:
    return tracker_root(repo_root) / LEDGER_DIR_NAME

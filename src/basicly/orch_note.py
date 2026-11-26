from __future__ import annotations

import importlib.util
import os
import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from . import owned_store

MODULE = "basicly_orch_note"


def _kit_note() -> Any:
    cached = sys.modules.get(MODULE)
    if cached is not None:
        return cached
    source = owned_store.packaged_kit_dir().parent / "orch" / "orch_state" / "note.py"
    spec = importlib.util.spec_from_file_location(MODULE, source) if source.is_file() else None
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules[MODULE] = module
    spec.loader.exec_module(module)
    return module


def note(
    cwd: Path,
    *,
    item: str | None = None,
    last: str | None = None,
    ok: bool = True,
    environ: Mapping[str, str] | None = None,
) -> bool:
    found = _kit_note()
    if found is None:
        return False
    return bool(
        found.note(cwd, os.environ if environ is None else environ, item=item, last=last, ok=ok)
    )


def pace(cwd: Path, record: str, moment: str, environ: Mapping[str, str] | None = None) -> bool:
    found = _kit_note()
    if found is None or not hasattr(found, "pace"):
        return False
    return bool(found.pace(cwd, os.environ if environ is None else environ, record, moment))

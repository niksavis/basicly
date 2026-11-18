from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

KIT = Path(__file__).resolve().parents[1] / ".basicly" / "core" / "kit" / "tracker"


def load(path: Path, name: str) -> ModuleType:
    cached = sys.modules.get(name)
    if cached is not None:
        return cached
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module

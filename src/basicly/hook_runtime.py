from __future__ import annotations

import importlib.util
import sys
from functools import cache
from typing import Any

from .catalog import bundled_catalog_root


@cache
def load(name: str) -> Any:
    source = bundled_catalog_root() / "hooks" / f"{name}.py"
    spec = importlib.util.spec_from_file_location(f"basicly_hook_{name}", source)
    if spec is None or spec.loader is None:
        raise ValueError("hook runtime is missing; reinstall basicly")
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module

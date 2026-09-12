from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType

from basicly import models

REPO_ROOT = Path(__file__).parent.parent
KIT = REPO_ROOT / ".basicly/core/kit/tier/tier_resolver.py"
MAP = REPO_ROOT / ".basicly" / "core" / "models" / "model-map.json"
REFERENCE_MAP: dict = json.loads(MAP.read_text(encoding="utf-8"))

UNAVAILABLE_VENDOR = "moonshotai"
UNAVAILABLE_TIER = "low"
NEIGHBOUR_TIER = "medium"
COPILOT_SURFACE = "github-copilot"


def _load_kit(path: Path = KIT) -> ModuleType:

    name = f"tier_resolver_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load_kit()


def _resolver(default_tier: str | None = None):
    resolver = kit.TierResolver.from_map_path(MAP, default_tier=default_tier)
    assert resolver is not None
    return resolver


def _definition(path: Path, tier: str | None = None) -> Path:
    lines = ["---", "name: my-own-agent", "description: An agent basicly never shipped."]
    if tier is not None:
        lines.append(f"tier: {tier}")
    lines += ["---", "", "Do the thing.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _expected(tier: str, vendor: str, surface: str) -> str:
    return models.model_for(tier, vendor, surface, mapping=REFERENCE_MAP)

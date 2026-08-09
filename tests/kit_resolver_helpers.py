"""Shared fixtures for the tier-resolver kit suites (basicly-u2hl.36).

Split out when the module-size ratchet refused ``test_kit_resolver.py``: three
suites now exercise the kit from different angles — its contract, how it finds
what it reads, and its isolation from basicly — and all three need the same
loader, the same map and the same expectations.

The loader is the load-bearing piece. It loads the kit the way a hook does, by
file path as a standalone module, and registering it in ``sys.modules`` before
execution is not optional: ``dataclasses`` resolves a string annotation through
``sys.modules[cls.__module__]``, so a module absent from it fails at
class-definition time.

Every expected model id comes from ``basicly.models`` or from the committed map
indexed directly, never retyped: the map is regenerated data, so a literal here
would turn a legitimate upstream change into a puzzle in a test file.
"""

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

# have a model there — the pair that makes "nothing" and "some other tier's
# model" distinguishable outcomes rather than a claim.
UNAVAILABLE_VENDOR = "moonshotai"
UNAVAILABLE_TIER = "low"
NEIGHBOUR_TIER = "medium"
COPILOT_SURFACE = "github-copilot"


def _load_kit(path: Path = KIT) -> ModuleType:
    """Load the kit the way a hook does: by file path, as a standalone module.

    Registering the module in ``sys.modules`` before executing it is the recipe
    the importlib docs give and is not optional here — ``dataclasses`` resolves a
    string annotation through ``sys.modules[cls.__module__]``, so a module absent
    from it fails at class-definition time. The kit's docstring says so, and this
    loader is the pinned copy of that instruction.
    """
    name = f"tier_resolver_{path.parent.name}"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


kit = _load_kit()


def _resolver(default_tier: str | None = None):
    """A resolver over the committed map."""
    resolver = kit.TierResolver.from_map_path(MAP, default_tier=default_tier)
    assert resolver is not None
    return resolver


def _definition(path: Path, tier: str | None = None) -> Path:
    """An agent definition written by a consumer, with or without a tier."""
    lines = ["---", "name: my-own-agent", "description: An agent basicly never shipped."]
    if tier is not None:
        lines.append(f"tier: {tier}")
    lines += ["---", "", "Do the thing.", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def _expected(tier: str, vendor: str, surface: str) -> str:
    """The in-harness resolver's answer for one cell, as the pinned expectation."""
    return models.model_for(tier, vendor, surface, mapping=REFERENCE_MAP)

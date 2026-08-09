"""Shared loading and fixtures for the model-map generator suites (basicly-u2hl.36).

Split out when the module-size ratchet refused ``test_model_map.py``. The
generator is now three modules — the anchor source, the resolution layer, and the
artifact assembly with its CLI — and each has a suite of its own, so the loader
and the paths they all need live here.

The loader does two things that are not optional. It registers each module in
``sys.modules`` before executing it, because ``@dataclass`` resolves its defining
module by name; and it puts ``.scripts`` on ``sys.path``, because the generator
imports its two siblings by plain name — which is what happens for free when the
script is run directly and has to be arranged when it is loaded by path.
"""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import yaml

REPO = Path(__file__).resolve().parents[1]
SCRIPTS = REPO / ".scripts"
MODELS_DIR = REPO / ".basicly" / "core" / "models"
ANCHORS_PATH = MODELS_DIR / "anchors.yaml"
MAP_PATH = MODELS_DIR / "model-map.json"
SCHEMA_PATH = MODELS_DIR / "model-map.schema.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "modelsdev-api.json"

# The vendors the map is required to cover, and the broker surface every one of
# them is also resolved onto. Spelled out rather than read from anchors.yaml: an
# assertion derived from its subject cannot fail when the subject shrinks.
REQUIRED_VENDORS = ("anthropic", "openai", "moonshotai", "google")
BROKER_SURFACE = "github-copilot"


def load_script(name: str):
    """Load one of the generator's modules from ``.scripts`` by module name."""
    if str(SCRIPTS) not in sys.path:
        sys.path.insert(0, str(SCRIPTS))
    spec = importlib.util.spec_from_file_location(name, SCRIPTS / f"{name}.py")
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


anchors_module = load_script("model_map_anchors")
resolve_module = load_script("model_map_resolve")
generator = load_script("generate_model_map")


def read_payload() -> dict[str, Any]:
    """The captured models.dev document, parsed fresh so a mutation cannot leak."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def read_anchors():
    """The repo's real anchor source — the thing the map is generated from."""
    return generator.load_anchors(ANCHORS_PATH)


def read_committed() -> dict[str, Any]:
    """The committed map."""
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def read_declared() -> dict[str, Any]:
    """The raw anchor source, as a reviewer reads it."""
    return yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))


def make_workspace(tmp_path: Path) -> Path:
    """A models dir holding the real anchors and a map built from the fixture."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "anchors.yaml").write_text(
        ANCHORS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    built = generator.build_map(FIXTURE_PATH.read_bytes(), None, read_anchors())
    (models_dir / "model-map.json").write_text(generator.render(built), encoding="utf-8")
    return models_dir


def run_cli(models_dir: Path, *extra: str) -> int:
    """Invoke the script's entry point against a workspace and the fixture."""
    return generator.main([
        "--models-dir",
        str(models_dir),
        "--payload",
        str(FIXTURE_PATH),
        *extra,
    ])


def cells(document: dict[str, Any]):
    """Yield every (tier, vendor, surface, entry) cell of a map."""
    for tier, entry in document["tiers"].items():
        for vendor, vendor_entry in entry["vendors"].items():
            for surface, served in vendor_entry["surfaces"].items():
                yield tier, vendor, surface, served

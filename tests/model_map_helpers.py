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

REQUIRED_VENDORS = ("anthropic", "openai", "moonshotai", "google")
BROKER_SURFACE = "github-copilot"


def load_script(name: str):
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
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


def read_anchors():
    return generator.load_anchors(ANCHORS_PATH)


def read_committed() -> dict[str, Any]:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


def read_declared() -> dict[str, Any]:
    return yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))


def make_workspace(tmp_path: Path) -> Path:
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "anchors.yaml").write_text(
        ANCHORS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    built = generator.build_map(FIXTURE_PATH.read_bytes(), None, read_anchors())
    (models_dir / "model-map.json").write_text(generator.render(built), encoding="utf-8")
    return models_dir


def run_cli(models_dir: Path, *extra: str) -> int:
    return generator.main([
        "--models-dir",
        str(models_dir),
        "--payload",
        str(FIXTURE_PATH),
        *extra,
    ])


def cells(document: dict[str, Any]):
    for tier, entry in document["tiers"].items():
        for vendor, vendor_entry in entry["vendors"].items():
            for surface, served in vendor_entry["surfaces"].items():
                yield tier, vendor, surface, served

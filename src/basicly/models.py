from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path

from .catalog import bundled_catalog_root
from .schema import MODEL_TIERS

MODELS_DIRNAME = "models"
MAP_FILENAME = "model-map.json"

LOCAL_CATALOG_DIR = Path(".basicly") / "core"

FAMILY_MODEL_SURFACES: dict[str, tuple[str, str]] = {
    "claude": ("anthropic", "anthropic"),
    "codex": ("openai", "openai"),
    "copilot": ("github-copilot", "anthropic"),
}


class ModelMapError(RuntimeError):
    pass


class ModelUnavailableError(LookupError):
    pass


class ModelResolutionError(RuntimeError):
    pass


def map_path(repo_root: Path | None = None) -> Path:

    if repo_root is not None:
        local = repo_root / LOCAL_CATALOG_DIR / MODELS_DIRNAME / MAP_FILENAME
        if local.is_file():
            return local
    return bundled_catalog_root() / MODELS_DIRNAME / MAP_FILENAME


def load_map(repo_root: Path | None = None) -> dict:
    return load_map_from(map_path(repo_root))


def load_map_from(path: Path) -> dict:

    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise ModelMapError(f"cannot read the model map at '{path}': {exc}") from exc
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise ModelMapError(f"the model map at '{path}' is not valid JSON: {exc}") from exc
    if not isinstance(parsed, dict) or not isinstance(parsed.get("tiers"), dict):
        raise ModelMapError(f"the model map at '{path}' has no 'tiers' section")
    return parsed


def model_for(
    tier: str,
    vendor: str,
    surface: str,
    *,
    mapping: dict | None = None,
    repo_root: Path | None = None,
) -> str:

    if tier not in MODEL_TIERS:
        raise ModelUnavailableError(f"unknown model tier {tier!r}; known: {list(MODEL_TIERS)}")
    data = mapping if mapping is not None else load_map(repo_root)
    tiers = data.get("tiers", {})
    entry = tiers.get(tier)
    if not isinstance(entry, dict):
        raise ModelUnavailableError(f"the model map carries no tier {tier!r}")
    vendors = entry.get("vendors")
    vendor_entry = vendors.get(vendor) if isinstance(vendors, dict) else None
    if not isinstance(vendor_entry, dict):
        raise ModelUnavailableError(f"the model map carries no vendor {vendor!r} for tier {tier!r}")
    surfaces = vendor_entry.get("surfaces")
    cell = surfaces.get(surface) if isinstance(surfaces, dict) else None
    if not isinstance(cell, dict):
        raise ModelUnavailableError(
            f"the model map carries no surface {surface!r} for {vendor!r} tier {tier!r}"
        )
    model = cell.get("model")
    if cell.get("status") != "available" or not isinstance(model, str) or not model:
        reason = cell.get("reason") or "the map marks this cell unavailable"
        raise ModelUnavailableError(f"{vendor} {tier} is unavailable on {surface}: {reason}")
    return model


@dataclass(frozen=True)
class ModelResolution:
    model: str | None = None
    tier: str | None = None
    source: str | None = None
    honoured: bool = True
    note: str | None = None


_ALIAS_RE = re.compile(r"[^a-z0-9]+")


def _normalize(model: str) -> str:

    return _ALIAS_RE.sub("-", model.strip().lower()).strip("-")


def same_model(pinned: str, observed: str) -> bool:

    left, right = _normalize(pinned), _normalize(observed)
    if left == right:
        return True
    if right.startswith(f"{left}-") or left.startswith(f"{right}-"):
        return True
    if "-" not in left and left.isalpha():
        return left in right.split("-")
    return False

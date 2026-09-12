from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from basicly.schema import MODEL_TIERS

ANCHORS_SCHEMA_VERSION = 3


class ResolutionError(RuntimeError):
    pass


@dataclass(frozen=True)
class GeneralModelRule:
    require_text_input: bool
    require_text_only_output: bool
    require_tool_call: bool

    def failures(self, record: Mapping[str, Any]) -> list[str]:
        modalities = record.get("modalities")
        modalities = modalities if isinstance(modalities, Mapping) else {}
        inputs = raw if isinstance(raw := modalities.get("input"), list) else []
        outputs = raw if isinstance(raw := modalities.get("output"), list) else []
        failed: list[str] = []
        if self.require_text_input and "text" not in inputs:
            failed.append(f"takes no text input (modalities.input={inputs})")
        if self.require_text_only_output and outputs != ["text"]:
            failed.append(f"does not emit text only (modalities.output={outputs})")
        if self.require_tool_call and record.get("tool_call") is not True:
            failed.append(f"does not support tool calls (tool_call={record.get('tool_call')!r})")
        return failed


@dataclass(frozen=True)
class Surface:
    id: str
    consumed_by: str
    accepts: str
    verified: str


@dataclass(frozen=True)
class Vendor:
    id: str
    name: str
    surfaces: tuple[str, ...]
    tiers: Mapping[str, str]
    collapse: Mapping[str, str]
    collapse_reason: str


@dataclass(frozen=True)
class Anchors:
    surfaces: Mapping[str, Surface]
    vendors: tuple[Vendor, ...]
    rule: GeneralModelRule
    tier_vendor_order: Mapping[str, tuple[str, ...]]


def require_mapping(value: object, where: str) -> Mapping[str, Any]:
    if not isinstance(value, Mapping):
        raise ResolutionError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def require_number(record: Mapping[str, Any], key: str, where: str) -> float | int:
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ResolutionError(f"{where} has a non-numeric '{key}': {value!r}")
    return value


def require_text(record: Mapping[str, Any], key: str, where: str) -> str:
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResolutionError(f"{where} must have a non-empty '{key}'")
    return value.strip()


def load_anchors(path: Path) -> Anchors:

    raw = require_mapping(yaml.safe_load(path.read_text(encoding="utf-8")), str(path))

    version = raw.get("schema_version")
    if version != ANCHORS_SCHEMA_VERSION:
        raise ResolutionError(
            f"{path}: unsupported schema_version {version!r} (expected {ANCHORS_SCHEMA_VERSION})"
        )

    surfaces = _load_surfaces(raw.get("surfaces"), path)
    rule = _load_rule(raw.get("general_model_rule"), path)
    vendors = _load_vendors(raw.get("vendors"), surfaces, path)
    order = _load_tier_vendor_order(raw.get("tier_vendor_order"), vendors, path)
    return Anchors(surfaces=surfaces, vendors=vendors, rule=rule, tier_vendor_order=order)


def _load_tier_vendor_order(
    value: object, vendors: tuple[Vendor, ...], path: Path
) -> Mapping[str, tuple[str, ...]]:

    table = require_mapping(value, f"{path}: 'tier_vendor_order'")
    known = [vendor.id for vendor in vendors]
    order: dict[str, tuple[str, ...]] = {}
    for tier in MODEL_TIERS:
        listed = table.get(tier)
        if not isinstance(listed, list) or not listed:
            raise ResolutionError(
                f"{path}: 'tier_vendor_order' must have a non-empty list for tier {tier!r}"
            )
        ids = [str(entry) for entry in listed]
        if sorted(ids) != sorted(known):
            raise ResolutionError(
                f"{path}: 'tier_vendor_order.{tier}' must list every vendor exactly once; "
                f"got {ids}, declared {known}"
            )
        order[tier] = tuple(ids)
    unknown_tiers = sorted(set(table) - set(MODEL_TIERS))
    if unknown_tiers:
        raise ResolutionError(
            f"{path}: 'tier_vendor_order' has unknown tier(s) {unknown_tiers}; "
            f"the vocabulary is {list(MODEL_TIERS)}"
        )
    return order


def _load_surfaces(value: object, path: Path) -> Mapping[str, Surface]:
    table = require_mapping(value, f"{path}: 'surfaces'")
    if not table:
        raise ResolutionError(f"{path}: 'surfaces' must not be empty")
    surfaces: dict[str, Surface] = {}
    for name, entry in table.items():
        where = f"{path}: surfaces['{name}']"
        record = require_mapping(entry, where)
        surfaces[str(name)] = Surface(
            id=str(name),
            consumed_by=require_text(record, "consumed_by", where),
            accepts=require_text(record, "accepts", where),
            verified=require_text(record, "verified", where),
        )
    return surfaces


def _load_rule(value: object, path: Path) -> GeneralModelRule:
    record = require_mapping(value, f"{path}: 'general_model_rule'")
    flags = {}
    for key in ("require_text_input", "require_text_only_output", "require_tool_call"):
        flag = record.get(key)
        if not isinstance(flag, bool):
            raise ResolutionError(f"{path}: general_model_rule['{key}'] must be true or false")
        flags[key] = flag
    return GeneralModelRule(**flags)


def _load_vendors(value: object, surfaces: Mapping[str, Surface], path: Path) -> tuple[Vendor, ...]:
    if not isinstance(value, list) or not value:
        raise ResolutionError(f"{path}: 'vendors' must be a non-empty list")
    vendors = [_load_vendor(entry, index, surfaces, path) for index, entry in enumerate(value)]
    ids = [vendor.id for vendor in vendors]
    if len(set(ids)) != len(ids):
        raise ResolutionError(f"{path}: duplicate vendor id in {ids}")
    return tuple(vendors)


def _load_vendor(entry: object, index: int, surfaces: Mapping[str, Surface], path: Path) -> Vendor:
    where = f"{path}: vendors[{index}]"
    record = require_mapping(entry, where)
    vendor_id = require_text(record, "id", where)
    where = f"{path}: vendor '{vendor_id}'"

    names = record.get("surfaces")
    if not isinstance(names, list) or not names:
        raise ResolutionError(f"{where}: 'surfaces' must be a non-empty list")
    unknown = [name for name in names if name not in surfaces]
    if unknown:
        raise ResolutionError(f"{where}: undeclared surfaces {unknown}")
    if len(set(names)) != len(names):
        raise ResolutionError(f"{where}: duplicate surface in {names}")
    if vendor_id not in names:
        raise ResolutionError(f"{where}: 'surfaces' must include the vendor's own id")

    tiers = require_mapping(record.get("tiers"), f"{where}: 'tiers'")
    missing = [tier for tier in MODEL_TIERS if tier not in tiers]
    unknown = sorted(set(tiers) - set(MODEL_TIERS))
    if missing or unknown:
        raise ResolutionError(
            f"{where}: 'tiers' must declare exactly {list(MODEL_TIERS)} — "
            f"missing {missing}, unknown {unknown}"
        )
    for tier in tiers:
        require_text(tiers, tier, f"{where}: tier '{tier}'")

    collapse = _load_collapse(record, tiers, where)
    return Vendor(
        id=vendor_id,
        name=require_text(record, "name", where),
        surfaces=tuple(str(name) for name in names),
        tiers={tier: str(tiers[tier]).strip() for tier in MODEL_TIERS},
        collapse=collapse,
        collapse_reason=str(record.get("collapse_reason", "")).strip(),
    )


def _load_collapse(
    record: Mapping[str, Any], tiers: Mapping[str, Any], where: str
) -> Mapping[str, str]:

    raw = record.get("collapse")
    if raw is None:
        return {}
    table = require_mapping(raw, f"{where}: 'collapse'")
    collapse: dict[str, str] = {}
    for tier, target in table.items():
        if tier not in MODEL_TIERS or target not in MODEL_TIERS:
            raise ResolutionError(
                f"{where}: collapse '{tier}' -> '{target}' names a tier outside {list(MODEL_TIERS)}"
            )
        if tier == target:
            raise ResolutionError(f"{where}: collapse '{tier}' cannot target itself")
        if tiers[tier] != tiers[target]:
            raise ResolutionError(
                f"{where}: collapse claims '{tier}' is '{target}' but they name different "
                f"models ({tiers[tier]!r} vs {tiers[target]!r})"
            )
        collapse[str(tier)] = str(target)
    if collapse and not str(record.get("collapse_reason", "")).strip():
        raise ResolutionError(f"{where}: a declared collapse needs a 'collapse_reason'")
    return collapse

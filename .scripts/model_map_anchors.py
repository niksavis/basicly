"""The reviewed anchor source: what a human declares, and what makes it valid.

Split out of ``generate_model_map.py`` (basicly-u2hl.36). The seam is the one the
map itself draws: ``anchors.yaml`` is hand-authored input with its own vocabulary
and its own validation rules, while ``model-map.json`` is derived output with its
own shape. Nothing here reads the network or the upstream payload — this module
answers "is what the human wrote well-formed", and it can answer that with no
models.dev document in hand at all.

The three ``require_*`` helpers are public rather than private because they are
this trio's shared validation vocabulary: ``model_map_resolve`` and the generator
both raise :class:`ResolutionError` through them, and a check that names *where*
it failed is the whole reason they exist.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

from basicly.schema import MODEL_TIERS

# The anchors source format this loader reads.
ANCHORS_SCHEMA_VERSION = 3


class ResolutionError(RuntimeError):
    """An anchor could not be resolved, or resolved to something unusable."""


@dataclass(frozen=True)
class GeneralModelRule:
    """The capability floor an anchor must clear to be usable as a tier.

    Without it a sweep happily picks an image, TTS, embedding, realtime or video
    model: Google publishes 41 records of which 21 are not general text models.
    """

    require_text_input: bool
    require_text_only_output: bool
    require_tool_call: bool

    def failures(self, record: Mapping[str, Any]) -> list[str]:
        """Name every requirement this record fails (empty when it is usable)."""
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
    """One place a resolved model can be written. The key is a models.dev provider."""

    id: str
    consumed_by: str
    accepts: str
    verified: str


@dataclass(frozen=True)
class Vendor:
    """One model publisher: its anchors, the surfaces serving it, its collapses."""

    id: str
    name: str
    surfaces: tuple[str, ...]
    tiers: Mapping[str, str]
    collapse: Mapping[str, str]
    collapse_reason: str


@dataclass(frozen=True)
class Anchors:
    """The whole reviewed anchor source."""

    surfaces: Mapping[str, Surface]
    vendors: tuple[Vendor, ...]
    rule: GeneralModelRule
    # Per tier, the order its vendors are tried when a spawn pins none (D31).
    # Every value is a permutation of the vendor ids above, which is what
    # :func:`_load_tier_vendor_order` enforces — so a resolver walking one of
    # these lists can never meet a vendor the map has no cells for.
    tier_vendor_order: Mapping[str, tuple[str, ...]]


def require_mapping(value: object, where: str) -> Mapping[str, Any]:
    """Return ``value`` as a mapping or raise naming ``where``."""
    if not isinstance(value, Mapping):
        raise ResolutionError(f"{where} must be a mapping, got {type(value).__name__}")
    return value


def require_number(record: Mapping[str, Any], key: str, where: str) -> float | int:
    """Return a numeric field or raise naming the record it came from."""
    value = record.get(key)
    if isinstance(value, bool) or not isinstance(value, int | float):
        raise ResolutionError(f"{where} has a non-numeric '{key}': {value!r}")
    return value


def require_text(record: Mapping[str, Any], key: str, where: str) -> str:
    """Return a non-empty string field or raise naming ``where``."""
    value = record.get(key)
    if not isinstance(value, str) or not value.strip():
        raise ResolutionError(f"{where} must have a non-empty '{key}'")
    return value.strip()


# --- the anchor source -------------------------------------------------------


def load_anchors(path: Path) -> Anchors:
    """Load and validate the anchor source.

    Every vendor's tier keys must be exactly ``schema.MODEL_TIERS`` — the
    vocabulary has one definition and this file may not extend or shrink it.
    """
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
    """Validate the per-tier vendor walk order (D31).

    Each tier's order must be a *permutation* of the declared vendor ids: every
    vendor exactly once, none unknown, none missing. That is stricter than "a
    list of known vendors" on purpose, and each half of it catches a different
    editing mistake. An unknown id is a typo that would make the resolver skip a
    vendor it thinks it tried; a missing id is a vendor silently excluded from
    every fallback, which reads as "unavailable everywhere" at the spawn and has
    no other symptom. A duplicate is neither, but it means the reviewed order
    does not say what its author thought, so it is refused too.
    """
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
    """Validate the surface table, keyed by models.dev provider id."""
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
    """Validate the general-model rule, which must be stated explicitly."""
    record = require_mapping(value, f"{path}: 'general_model_rule'")
    flags = {}
    for key in ("require_text_input", "require_text_only_output", "require_tool_call"):
        flag = record.get(key)
        if not isinstance(flag, bool):
            raise ResolutionError(f"{path}: general_model_rule['{key}'] must be true or false")
        flags[key] = flag
    return GeneralModelRule(**flags)


def _load_vendors(value: object, surfaces: Mapping[str, Surface], path: Path) -> tuple[Vendor, ...]:
    """Validate the vendor list, its tier coverage, and its declared collapses."""
    if not isinstance(value, list) or not value:
        raise ResolutionError(f"{path}: 'vendors' must be a non-empty list")
    vendors = [_load_vendor(entry, index, surfaces, path) for index, entry in enumerate(value)]
    ids = [vendor.id for vendor in vendors]
    if len(set(ids)) != len(ids):
        raise ResolutionError(f"{path}: duplicate vendor id in {ids}")
    return tuple(vendors)


def _load_vendor(entry: object, index: int, surfaces: Mapping[str, Surface], path: Path) -> Vendor:
    """Validate one vendor entry."""
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
    # The vendor's own provider must be listed, so its native cost is always the
    # baseline a broker's markup is read against.
    if vendor_id not in names:
        raise ResolutionError(f"{where}: 'surfaces' must include the vendor's own id")

    tiers = require_mapping(record.get("tiers"), f"{where}: 'tiers'")
    # The set, not the order: the map's tier order comes from MODEL_TIERS either
    # way, so enforcing key order here would add a failure mode with no safety
    # value. A missing or invented tier is named so the fix needs no guessing.
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
        # Re-keyed in MODEL_TIERS order so the generated map's row order is the
        # vocabulary's, whatever order the source happens to list them in.
        tiers={tier: str(tiers[tier]).strip() for tier in MODEL_TIERS},
        collapse=collapse,
        collapse_reason=str(record.get("collapse_reason", "")).strip(),
    )


def _load_collapse(
    record: Mapping[str, Any], tiers: Mapping[str, Any], where: str
) -> Mapping[str, str]:
    """Validate a declared tier collapse against the ids it claims to describe.

    A collapse is a claim about the data; cross-checking it here is what stops the
    declaration and the ids drifting apart.
    """
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

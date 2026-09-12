from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from model_map_anchors import (
    Anchors,
    GeneralModelRule,
    ResolutionError,
    Vendor,
    require_mapping,
    require_number,
    require_text,
)

from basicly.schema import MODEL_TIERS

AVAILABLE = "available"
UNAVAILABLE = "unavailable"


def _provider_models(payload: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    entry = payload.get(provider)
    if entry is None:
        raise ResolutionError(f"provider '{provider}' is not in the models.dev payload")
    return require_mapping(
        require_mapping(entry, f"provider '{provider}'").get("models"),
        f"provider '{provider}' 'models'",
    )


def _check_general(record: Mapping[str, Any], rule: GeneralModelRule, where: str) -> None:
    failures = rule.failures(record)
    if failures:
        raise ResolutionError(f"{where} is not usable as a tier: it {'; and it '.join(failures)}")


def _match_by_name(
    models: Mapping[str, Any], model_name: str, provider: str, where: str
) -> str | None:

    matches = sorted(
        model_id for model_id, record in models.items() if record.get("name") == model_name
    )
    if not matches:
        return None
    if len(matches) > 1:
        raise ResolutionError(
            f"{where}: provider '{provider}' serves {len(matches)} models named "
            f"{model_name!r}: {matches}"
        )
    return matches[0]


def _serving_entry(record: Mapping[str, Any], model_id: str, provider: str) -> dict[str, Any]:
    where = f"'{provider}/{model_id}'"
    cost = require_mapping(record.get("cost"), f"{where} 'cost'")
    limit = require_mapping(record.get("limit"), f"{where} 'limit'")

    limits: dict[str, Any] = {"context": require_number(limit, "context", f"{where} 'limit'")}
    if "input" in limit:
        limits["input"] = require_number(limit, "input", f"{where} 'limit'")
    limits["output"] = require_number(limit, "output", f"{where} 'limit'")

    return {
        "status": AVAILABLE,
        "model": model_id,
        "cost_usd_per_mtok": {
            "input": require_number(cost, "input", f"{where} 'cost'"),
            "output": require_number(cost, "output", f"{where} 'cost'"),
        },
        "limit_tokens": limits,
        "upstream_last_updated": record.get("last_updated"),
    }


def _resolve_surface(
    payload: Mapping[str, Any],
    surface: str,
    model_name: str,
    rule: GeneralModelRule,
    where: str,
) -> dict[str, Any]:
    models = _provider_models(payload, surface)
    model_id = _match_by_name(models, model_name, surface, where)
    if model_id is None:
        return {
            "status": UNAVAILABLE,
            "reason": f"provider '{surface}' serves no model named {model_name!r}",
        }
    record = require_mapping(models[model_id], f"'{surface}/{model_id}'")
    _check_general(record, rule, f"{where}: '{surface}/{model_id}'")
    return _serving_entry(record, model_id, surface)


def _resolve_vendor_tier(
    payload: Mapping[str, Any], vendor: Vendor, tier: str, rule: GeneralModelRule
) -> dict[str, Any]:
    where = f"tier '{tier}' vendor '{vendor.id}'"
    anchor_id = vendor.tiers[tier]
    anchor_record = _provider_models(payload, vendor.id).get(anchor_id)
    if anchor_record is None:
        raise ResolutionError(
            f"{where}: anchor '{vendor.id}/{anchor_id}' no longer resolves upstream "
            f"(no such model id)"
        )
    anchor_record = require_mapping(anchor_record, f"'{vendor.id}/{anchor_id}'")
    _check_general(anchor_record, rule, f"{where}: anchor '{vendor.id}/{anchor_id}'")

    model_name = require_text(anchor_record, "name", f"{where}: anchor '{anchor_id}'")
    entry: dict[str, Any] = {
        "anchor": anchor_id,
        "model_name": model_name,
        "family": anchor_record.get("family"),
        "reasoning": anchor_record.get("reasoning"),
    }
    if tier in vendor.collapse:
        entry["collapse"] = {
            "same_model_as_tier": vendor.collapse[tier],
            "reason": vendor.collapse_reason,
        }
    entry["surfaces"] = {
        surface: _resolve_surface(payload, surface, model_name, rule, where)
        for surface in vendor.surfaces
    }
    return entry


def resolve_tiers(payload: Mapping[str, Any], anchors: Anchors) -> dict[str, Any]:
    return {
        tier: {
            "vendor_order": list(anchors.tier_vendor_order[tier]),
            "vendors": {
                vendor.id: _resolve_vendor_tier(payload, vendor, tier, anchors.rule)
                for vendor in anchors.vendors
            },
        }
        for tier in MODEL_TIERS
    }

"""Resolve each anchored model against the upstream payload, cell by cell.

Split out of ``generate_model_map.py`` (basicly-u2hl.36), between the anchor
source that declares what we want and the artifact assembly that writes what we
got. Everything here answers one question — does this provider serve this
anchor, and on what terms — so an unserved anchor becomes an explicit
``unavailable`` cell with a reason rather than a silent substitution.

Depends on :mod:`model_map_anchors` and never the other way round: validation of
what a human wrote cannot depend on what upstream happens to serve today.
"""

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

# What a resolved cell's `status` says. A cell is one or the other, never absent.
AVAILABLE = "available"
UNAVAILABLE = "unavailable"


def _provider_models(payload: Mapping[str, Any], provider: str) -> Mapping[str, Any]:
    """Return one provider's model records or raise naming the provider."""
    entry = payload.get(provider)
    if entry is None:
        raise ResolutionError(f"provider '{provider}' is not in the models.dev payload")
    return require_mapping(
        require_mapping(entry, f"provider '{provider}'").get("models"),
        f"provider '{provider}' 'models'",
    )


def _check_general(record: Mapping[str, Any], rule: GeneralModelRule, where: str) -> None:
    """Refuse a model that is not a general text tool-calling model."""
    failures = rule.failures(record)
    if failures:
        raise ResolutionError(f"{where} is not usable as a tier: it {'; and it '.join(failures)}")


def _match_by_name(
    models: Mapping[str, Any], model_name: str, provider: str, where: str
) -> str | None:
    """The single id ``provider`` serves ``model_name`` under, or None.

    None means genuinely unavailable. Two matches is a guess, so it raises rather
    than picking one.
    """
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
    """The serving data one surface publishes for one model."""
    where = f"'{provider}/{model_id}'"
    cost = require_mapping(record.get("cost"), f"{where} 'cost'")
    limit = require_mapping(record.get("limit"), f"{where} 'limit'")

    limits: dict[str, Any] = {"context": require_number(limit, "context", f"{where} 'limit'")}
    # Only some providers publish a separate input cap, and not for every model
    # (github-copilot's claude-sonnet-5 has none), so it is optional. Where it is
    # absent, `context` governs the input side.
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
    """Resolve one (model, surface) pair to a serving entry or an explicit gap."""
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
    """Resolve one (tier, vendor) anchor across every surface serving that vendor."""
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
    """Resolve every (tier, vendor, surface) cell, and publish each tier's walk order."""
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

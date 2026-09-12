from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly.schema import MODEL_TIERS
from tests.kit_resolver_helpers import (
    COPILOT_SURFACE,
    MAP,
    NEIGHBOUR_TIER,
    REFERENCE_MAP,
    UNAVAILABLE_TIER,
    UNAVAILABLE_VENDOR,
    _definition,
    _expected,
    _resolver,
    kit,
)


def test_a_consumer_authored_definition_resolves_its_declared_tier(tmp_path: Path) -> None:
    definition = _definition(tmp_path / "elsewhere" / "my-own-agent.md", tier="high")
    result = _resolver().resolve("claude", definition=definition)
    assert result.model == _expected("high", "anthropic", "anthropic")
    assert (result.tier, result.source, result.surface, result.vendor) == (
        "high",
        "definition",
        "anthropic",
        "anthropic",
    )
    assert result.reason is None


def test_a_catalog_definition_and_a_consumer_definition_resolve_identically(
    tmp_path: Path,
) -> None:
    catalog_style = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="medium")
    consumer_style = _definition(tmp_path / "somewhere" / "else.md", tier="medium")
    resolver = _resolver()
    assert (
        resolver.resolve("claude", definition=catalog_style).model
        == resolver.resolve("claude", definition=consumer_style).model
        == _expected("medium", "anthropic", "anthropic")
    )


def test_one_declared_tier_resolves_to_each_hosts_own_spelling(tmp_path: Path) -> None:

    definition = _definition(tmp_path / "my-own-agent.md", tier="low")
    resolver = _resolver()
    claude = resolver.resolve("claude", definition=definition)
    copilot = resolver.resolve("copilot", definition=definition)
    assert claude.model == _expected("low", "anthropic", "anthropic")
    assert copilot.model == _expected("low", "anthropic", COPILOT_SURFACE)
    assert (claude.surface, copilot.surface) == ("anthropic", COPILOT_SURFACE)
    assert claude.model != copilot.model


def test_a_vendor_override_resolves_the_copilot_surface_for_that_vendor(
    tmp_path: Path,
) -> None:
    definition = _definition(tmp_path / "my-own-agent.md", tier="medium")
    result = _resolver().resolve("copilot", definition=definition, vendor=UNAVAILABLE_VENDOR)
    assert result.model == _expected("medium", UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert result.vendor == UNAVAILABLE_VENDOR


def test_an_explicit_tier_outranks_the_definition(tmp_path: Path) -> None:
    definition = _definition(tmp_path / "my-own-agent.md", tier="low")
    result = _resolver().resolve("claude", definition=definition, tier="high")
    assert (result.tier, result.source) == ("high", "argument")
    assert result.model == _expected("high", "anthropic", "anthropic")


def test_an_unavailable_cell_resolves_to_nothing_not_a_neighbouring_tier(
    tmp_path: Path,
) -> None:
    definition = _definition(tmp_path / "my-own-agent.md", tier=UNAVAILABLE_TIER)
    resolver = _resolver()
    result = resolver.resolve("copilot", definition=definition, vendor=UNAVAILABLE_VENDOR)
    assert result.model is None
    assert result.reason is not None
    cell = REFERENCE_MAP["tiers"][UNAVAILABLE_TIER]["vendors"][UNAVAILABLE_VENDOR]["surfaces"][
        COPILOT_SURFACE
    ]
    assert cell["reason"] in result.reason
    neighbour = resolver.model_for(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert neighbour == _expected(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert result.model != neighbour


WALK_TIER = "high"


def _resolver_over(tmp_path: Path, mutate):
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    mutate(mapping)
    path = tmp_path / "model-map.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    resolver = kit.TierResolver.from_map_path(path)
    assert resolver is not None
    return resolver


def _make_unavailable(mapping: dict, tier: str, vendor: str, surface: str) -> None:
    cell = mapping["tiers"][tier]["vendors"][vendor]["surfaces"][surface]
    cell["status"] = "unavailable"
    cell.pop("model", None)
    cell["reason"] = f"injected: {vendor} does not serve {tier} on {surface}"


def test_the_walk_falls_to_the_next_vendor_when_the_first_is_unavailable(
    tmp_path: Path,
) -> None:
    order = REFERENCE_MAP["tiers"][WALK_TIER]["vendor_order"]
    first, second = order[0], order[1]
    resolver = _resolver_over(
        tmp_path, lambda m: _make_unavailable(m, WALK_TIER, first, COPILOT_SURFACE)
    )

    result = resolver.resolve("copilot", tier=WALK_TIER)

    assert result.vendor == second
    assert result.model == _expected(WALK_TIER, second, COPILOT_SURFACE)
    assert result.reason is None
    assert [vendor for vendor, _ in result.skipped] == [first]
    assert "injected" in result.skipped[0][1]
    assert _resolver().resolve("copilot", tier=WALK_TIER).vendor == first


def test_the_walk_never_reaches_another_tier(tmp_path: Path) -> None:
    order = REFERENCE_MAP["tiers"][WALK_TIER]["vendor_order"]

    def blackout(mapping: dict) -> None:
        for vendor in order:
            _make_unavailable(mapping, WALK_TIER, vendor, COPILOT_SURFACE)

    resolver = _resolver_over(tmp_path, blackout)
    result = resolver.resolve("copilot", tier=WALK_TIER)

    assert result.model is None
    assert result.reason is not None
    for vendor in order:
        assert vendor in result.reason, f"the refusal must name {vendor} among what it tried"
    assert [vendor for vendor, _ in result.skipped] == list(order)
    neighbour = resolver.resolve("copilot", tier=NEIGHBOUR_TIER)
    assert neighbour.model is not None
    assert result.model != neighbour.model


def test_a_pinned_vendor_never_walks(tmp_path: Path) -> None:

    order = REFERENCE_MAP["tiers"][WALK_TIER]["vendor_order"]
    first = order[0]
    resolver = _resolver_over(
        tmp_path, lambda m: _make_unavailable(m, WALK_TIER, first, COPILOT_SURFACE)
    )

    result = resolver.resolve("copilot", tier=WALK_TIER, vendor=first)

    assert result.model is None
    assert result.vendor == first
    assert result.skipped == ()


def test_a_map_with_no_walk_order_still_resolves_the_hosts_default_vendor(
    tmp_path: Path,
) -> None:

    def strip_order(mapping: dict) -> None:
        for tier in mapping["tiers"].values():
            tier.pop("vendor_order", None)

    resolver = _resolver_over(tmp_path, strip_order)
    result = resolver.resolve("copilot", tier=WALK_TIER)

    assert result.model == _resolver().resolve("copilot", tier=WALK_TIER).model
    assert result.skipped == ()


def test_the_committed_walk_order_lists_every_vendor_exactly_once() -> None:

    vendors = sorted(REFERENCE_MAP["vendors"])
    for tier, entry in REFERENCE_MAP["tiers"].items():
        assert sorted(entry["vendor_order"]) == vendors, f"{tier} walk order is not a permutation"
        assert sorted(entry["vendors"]) == vendors, f"{tier} vendor cells do not match the roster"


def test_an_undeclared_tier_with_no_default_resolves_to_nothing(tmp_path: Path) -> None:
    definition = _definition(tmp_path / "my-own-agent.md")
    result = _resolver().resolve("claude", definition=definition)
    assert (result.model, result.tier, result.source) == (None, None, None)
    assert result.reason is not None and "no default tier is configured" in result.reason


def test_an_undeclared_tier_uses_the_configured_default_when_there_is_one(
    tmp_path: Path,
) -> None:
    definition = _definition(tmp_path / "my-own-agent.md")
    result = _resolver(default_tier="low").resolve("claude", definition=definition)
    assert (result.tier, result.source) == ("low", "default")
    assert result.model == _expected("low", "anthropic", "anthropic")


def test_a_missing_definition_file_resolves_to_nothing(tmp_path: Path) -> None:
    result = _resolver().resolve("claude", definition=tmp_path / "absent.md")
    assert result.model is None
    assert kit.declared_tier(tmp_path / "absent.md") is None


def test_a_tier_the_map_does_not_carry_resolves_to_nothing() -> None:
    result = _resolver().resolve("claude", tier="enormous")
    assert result.model is None
    assert result.reason is not None and "enormous" in result.reason
    for tier in MODEL_TIERS:
        assert tier in result.reason


def test_an_unknown_host_resolves_to_nothing() -> None:
    result = _resolver().resolve("some-future-host", tier="high")
    assert (result.model, result.surface, result.vendor) == (None, None, None)
    assert result.reason is not None and "unknown host" in result.reason


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{ not json", id="not-json"),
        pytest.param("[]", id="not-an-object"),
        pytest.param('{"tier_order": ["low"]}', id="no-tiers-section"),
    ],
)
def test_a_map_that_cannot_be_used_yields_no_resolver(tmp_path: Path, payload: str) -> None:
    broken = tmp_path / "model-map.json"
    broken.write_text(payload, encoding="utf-8")
    assert kit.load_map(broken) is None
    assert kit.TierResolver.from_map_path(broken) is None


def test_an_absent_map_yields_no_resolver(tmp_path: Path) -> None:
    assert kit.load_map(tmp_path / "model-map.json") is None
    assert kit.TierResolver.from_map_path(tmp_path / "model-map.json") is None

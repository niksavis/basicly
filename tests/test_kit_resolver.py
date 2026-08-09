"""The tier resolver kit's contract: what a declared tier resolves to.

Split from the original suite by the module-size ratchet (basicly-u2hl.36). This
file holds the acceptance criteria — a declared tier resolves for the requested
host surface, an unavailable cell resolves to nothing rather than to a
neighbour, and the vendor walk (D31) survives a vendor going dark. Discovery and
the no-basicly isolation proof live in the sibling suites.
"""

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

# --- the AC: a declared tier resolves for the requested host surface ----------


def test_a_consumer_authored_definition_resolves_its_declared_tier(tmp_path: Path) -> None:
    """A definition basicly never shipped, outside any catalog, resolves normally."""
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
    """Resolution keys off the file, so where the file came from cannot change it."""
    catalog_style = _definition(tmp_path / ".claude" / "agents" / "my-own-agent.md", tier="medium")
    consumer_style = _definition(tmp_path / "somewhere" / "else.md", tier="medium")
    resolver = _resolver()
    assert (
        resolver.resolve("claude", definition=catalog_style).model
        == resolver.resolve("claude", definition=consumer_style).model
        == _expected("medium", "anthropic", "anthropic")
    )


def test_one_declared_tier_resolves_to_each_hosts_own_spelling(tmp_path: Path) -> None:
    """Surface decides the spelling; the low anthropic tier is the pinned example.

    ``claude-haiku-4-5`` on the anthropic surface versus ``claude-haiku-4.5`` on
    github-copilot is the map README's canonical case, and copilot rejects the
    hyphenated form outright — so the two answers differing is the behaviour, not
    an incidental fact.
    """
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
    """Copilot is multi-vendor, so the default vendor is overridable per call."""
    definition = _definition(tmp_path / "my-own-agent.md", tier="medium")
    result = _resolver().resolve("copilot", definition=definition, vendor=UNAVAILABLE_VENDOR)
    assert result.model == _expected("medium", UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert result.vendor == UNAVAILABLE_VENDOR


def test_an_explicit_tier_outranks_the_definition(tmp_path: Path) -> None:
    """A caller that already knows the tier does not need the file consulted."""
    definition = _definition(tmp_path / "my-own-agent.md", tier="low")
    result = _resolver().resolve("claude", definition=definition, tier="high")
    assert (result.tier, result.source) == ("high", "argument")
    assert result.model == _expected("high", "anthropic", "anthropic")


# --- the AC: fail closed, never a substitution --------------------------------


def test_an_unavailable_cell_resolves_to_nothing_not_a_neighbouring_tier(
    tmp_path: Path,
) -> None:
    """The silent demotion basicly-izda exists to prevent, pinned as a test."""
    definition = _definition(tmp_path / "my-own-agent.md", tier=UNAVAILABLE_TIER)
    resolver = _resolver()
    result = resolver.resolve("copilot", definition=definition, vendor=UNAVAILABLE_VENDOR)
    assert result.model is None
    assert result.reason is not None
    # The map's own reason, carried through verbatim rather than flattened to
    # "not found", so the refusal names the real cause.
    cell = REFERENCE_MAP["tiers"][UNAVAILABLE_TIER]["vendors"][UNAVAILABLE_VENDOR]["surfaces"][
        COPILOT_SURFACE
    ]
    assert cell["reason"] in result.reason
    # Positive control: the neighbouring tier really does have a model on this
    # surface, so "nothing" and "the wrong model" are distinguishable outcomes.
    neighbour = resolver.model_for(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert neighbour == _expected(NEIGHBOUR_TIER, UNAVAILABLE_VENDOR, COPILOT_SURFACE)
    assert result.model != neighbour


# --- the vendor walk (D31, basicly-u2hl.35) -----------------------------------
#
# Every cell the walk would fall *to* is available on the shipped map, and every
# order starts with a vendor that serves every tier on both surfaces — so on real
# data the walk always stops at the first vendor and the fallback never executes.
# That is the healthy state, and it is exactly why these tests inject the gap
# instead of hunting for one: a fallback asserted only against today's map is a
# test that passes because the feature never ran.

WALK_TIER = "high"


def _resolver_over(tmp_path: Path, mutate):
    """A resolver over a private copy of the committed map, mutated by *mutate*."""
    mapping = json.loads(MAP.read_text(encoding="utf-8"))
    mutate(mapping)
    path = tmp_path / "model-map.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    resolver = kit.TierResolver.from_map_path(path)
    assert resolver is not None
    return resolver


def _make_unavailable(mapping: dict, tier: str, vendor: str, surface: str) -> None:
    """Turn one cell into the same shape the generator writes for an unserved model."""
    cell = mapping["tiers"][tier]["vendors"][vendor]["surfaces"][surface]
    cell["status"] = "unavailable"
    cell.pop("model", None)
    cell["reason"] = f"injected: {vendor} does not serve {tier} on {surface}"


def test_the_walk_falls_to_the_next_vendor_when_the_first_is_unavailable(
    tmp_path: Path,
) -> None:
    """The whole point of D31: a tier survives its first vendor going dark."""
    order = REFERENCE_MAP["tiers"][WALK_TIER]["vendor_order"]
    first, second = order[0], order[1]
    resolver = _resolver_over(
        tmp_path, lambda m: _make_unavailable(m, WALK_TIER, first, COPILOT_SURFACE)
    )

    result = resolver.resolve("copilot", tier=WALK_TIER)

    assert result.vendor == second
    assert result.model == _expected(WALK_TIER, second, COPILOT_SURFACE)
    assert result.reason is None
    # The skip is recorded, not silent: landing on the second vendor and landing
    # on the first are indistinguishable from the model alone.
    assert [vendor for vendor, _ in result.skipped] == [first]
    assert "injected" in result.skipped[0][1]
    # Control: unmutated, the same call stops at the first vendor, so the test
    # above is measuring the injection rather than the map's ordinary answer.
    assert _resolver().resolve("copilot", tier=WALK_TIER).vendor == first


def test_the_walk_never_reaches_another_tier(tmp_path: Path) -> None:
    """Sideways across vendors only. A tier resolves to itself or to nothing."""
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
    # Positive control: a neighbouring tier still has models on this surface, so
    # "nothing" is a refusal rather than an empty map.
    neighbour = resolver.resolve("copilot", tier=NEIGHBOUR_TIER)
    assert neighbour.model is not None
    assert result.model != neighbour.model


def test_a_pinned_vendor_never_walks(tmp_path: Path) -> None:
    """An explicit request is answered or refused on its own terms.

    Serving a different vendor than the caller named would be worse than saying
    no: the caller pinned it for a reason the resolver cannot see.
    """
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
    """The kit is copied into repos that may carry a schema-2 map.

    A tolerant read there degrades to the single-vendor behaviour that map was
    written for, rather than resolving to nothing and silently un-tiering every
    spawn in that repository.
    """

    def strip_order(mapping: dict) -> None:
        for tier in mapping["tiers"].values():
            tier.pop("vendor_order", None)

    resolver = _resolver_over(tmp_path, strip_order)
    result = resolver.resolve("copilot", tier=WALK_TIER)

    assert result.model == _resolver().resolve("copilot", tier=WALK_TIER).model
    assert result.skipped == ()


def test_the_committed_walk_order_lists_every_vendor_exactly_once() -> None:
    """The invariant the generator enforces, pinned on the shipped artifact.

    Checked here as well as in the generator because the map is consumed by
    harnesses that never run the generator, and a walk order missing a vendor
    excludes it from every fallback with no other symptom.
    """
    vendors = sorted(REFERENCE_MAP["vendors"])
    for tier, entry in REFERENCE_MAP["tiers"].items():
        assert sorted(entry["vendor_order"]) == vendors, f"{tier} walk order is not a permutation"
        assert sorted(entry["vendors"]) == vendors, f"{tier} vendor cells do not match the roster"


def test_an_undeclared_tier_with_no_default_resolves_to_nothing(tmp_path: Path) -> None:
    """No tier and no configured default is a refusal, not the cheapest tier."""
    definition = _definition(tmp_path / "my-own-agent.md")
    result = _resolver().resolve("claude", definition=definition)
    assert (result.model, result.tier, result.source) == (None, None, None)
    assert result.reason is not None and "no default tier is configured" in result.reason


def test_an_undeclared_tier_uses_the_configured_default_when_there_is_one(
    tmp_path: Path,
) -> None:
    """A default is honoured, and recorded as the input that decided the tier."""
    definition = _definition(tmp_path / "my-own-agent.md")
    result = _resolver(default_tier="low").resolve("claude", definition=definition)
    assert (result.tier, result.source) == ("low", "default")
    assert result.model == _expected("low", "anthropic", "anthropic")


def test_a_missing_definition_file_resolves_to_nothing(tmp_path: Path) -> None:
    """An absent definition declares no tier; it does not raise in the spawn path."""
    result = _resolver().resolve("claude", definition=tmp_path / "absent.md")
    assert result.model is None
    assert kit.declared_tier(tmp_path / "absent.md") is None


def test_a_tier_the_map_does_not_carry_resolves_to_nothing() -> None:
    """An unknown tier names the map's vocabulary instead of guessing at one."""
    result = _resolver().resolve("claude", tier="enormous")
    assert result.model is None
    assert result.reason is not None and "enormous" in result.reason
    for tier in MODEL_TIERS:
        assert tier in result.reason


def test_an_unknown_host_resolves_to_nothing() -> None:
    """A host with no known surface cannot be given a spelling, so it gets none."""
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
    """A broken map means "leave the spawn alone", never "every tier is unavailable"."""
    broken = tmp_path / "model-map.json"
    broken.write_text(payload, encoding="utf-8")
    assert kit.load_map(broken) is None
    assert kit.TierResolver.from_map_path(broken) is None


def test_an_absent_map_yields_no_resolver(tmp_path: Path) -> None:
    """The case a machine-wide hook hits in every repo that has no map."""
    assert kit.load_map(tmp_path / "model-map.json") is None
    assert kit.TierResolver.from_map_path(tmp_path / "model-map.json") is None

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from basicly.schema import MODEL_TIERS
from tests import model_map_helpers as helpers
from tests.model_map_helpers import (
    ANCHORS_PATH,
    BROKER_SURFACE,
    FIXTURE_PATH,
    MAP_PATH,
    REQUIRED_VENDORS,
    SCHEMA_PATH,
    generator,
)

_cells = helpers.cells
_run = helpers.run_cli


@pytest.fixture
def payload() -> dict[str, Any]:
    return helpers.read_payload()


@pytest.fixture
def anchors():
    return helpers.read_anchors()


@pytest.fixture
def committed() -> dict[str, Any]:
    return helpers.read_committed()


@pytest.fixture
def declared() -> dict[str, Any]:
    return helpers.read_declared()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    return helpers.make_workspace(tmp_path)


def test_committed_map_validates_against_its_published_schema(committed: dict) -> None:
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(committed), key=str)
    assert not errors, [f"{list(e.absolute_path)}: {e.message}" for e in errors]


def test_committed_map_covers_every_tier_for_every_required_vendor(committed: dict) -> None:
    assert committed["tier_order"] == list(MODEL_TIERS)
    assert tuple(committed["tiers"]) == MODEL_TIERS
    for tier in MODEL_TIERS:
        vendors = committed["tiers"][tier]["vendors"]
        for vendor in REQUIRED_VENDORS:
            assert vendor in vendors, f"tier '{tier}' has no '{vendor}' entry"


def test_every_vendor_is_resolved_on_its_own_surface_and_the_broker(committed: dict) -> None:
    for tier, vendor, _, _ in _cells(committed):
        surfaces = committed["tiers"][tier]["vendors"][vendor]["surfaces"]
        assert vendor in surfaces, f"{tier}/{vendor} is missing its own surface"
        assert BROKER_SURFACE in surfaces, f"{tier}/{vendor} is missing '{BROKER_SURFACE}'"


def test_committed_anchors_match_the_reviewed_anchor_source(
    committed: dict, declared: dict
) -> None:
    for vendor in declared["vendors"]:
        for tier, anchor in vendor["tiers"].items():
            entry = committed["tiers"][tier]["vendors"][vendor["id"]]
            assert entry["anchor"] == anchor, f"{tier}/{vendor['id']} anchor drifted"


def test_committed_surface_table_matches_the_anchor_source(committed: dict, declared: dict) -> None:
    assert list(committed["surfaces"]) == list(declared["surfaces"])
    for vendor in declared["vendors"]:
        assert committed["vendors"][vendor["id"]]["surfaces"] == list(vendor["surfaces"])


def test_committed_map_is_exactly_what_the_generator_renders(committed: dict) -> None:
    assert generator.render(committed) == MAP_PATH.read_text(encoding="utf-8")


def test_the_fixture_reproduces_the_committed_tiers(committed: dict, anchors) -> None:

    built = generator.build_map(FIXTURE_PATH.read_bytes(), None, anchors)
    assert built["tiers"] == committed["tiers"]


def test_each_surface_spells_the_same_model_its_own_way(payload: dict, anchors) -> None:
    tiers = generator.resolve_tiers(payload, anchors)
    surfaces = tiers["low"]["vendors"]["anthropic"]["surfaces"]
    assert surfaces["anthropic"]["model"] == "claude-haiku-4-5"
    assert surfaces[BROKER_SURFACE]["model"] == "claude-haiku-4.5"


def test_the_broker_now_prices_every_matched_model_as_its_native_surface(
    payload: dict, anchors
) -> None:

    tiers = generator.resolve_tiers(payload, anchors)
    diverging = {
        (tier, vendor)
        for tier, entry in tiers.items()
        for vendor, vendor_entry in entry["vendors"].items()
        for surface, cell in vendor_entry["surfaces"].items()
        if surface != BROKER_SURFACE
        and cell.get("status") == "available"
        and vendor_entry["surfaces"].get(BROKER_SURFACE, {}).get("status") == "available"
        and cell["cost_usd_per_mtok"]
        != vendor_entry["surfaces"][BROKER_SURFACE]["cost_usd_per_mtok"]
    }
    assert diverging == set(), (
        "the broker priced a model differently from its native surface again; "
        "that is an upstream change worth reading, not a test to re-pin"
    )


def test_an_unserved_tier_is_marked_unavailable_with_no_model_key(payload: dict, anchors) -> None:
    tiers = generator.resolve_tiers(payload, anchors)
    gap = tiers["low"]["vendors"]["moonshotai"]["surfaces"][BROKER_SURFACE]
    assert gap["status"] == "unavailable"
    assert "model" not in gap, "an unavailable cell must not carry a model id"
    assert "Kimi K2.6" in gap["reason"]

    served = tiers["medium"]["vendors"]["moonshotai"]["surfaces"][BROKER_SURFACE]
    assert served["status"] == "available"
    assert served["model"] == "kimi-k2.7-code"


def test_the_broker_gaps_are_exactly_the_measured_ones(payload: dict, anchors) -> None:

    tiers = generator.resolve_tiers(payload, anchors)
    gaps = sorted(
        (tier, vendor)
        for tier, entry in tiers.items()
        for vendor, vendor_entry in entry["vendors"].items()
        if vendor_entry["surfaces"][BROKER_SURFACE]["status"] == "unavailable"
    )
    assert gaps == [
        ("high", "google"),
        ("low", "google"),
        ("low", "moonshotai"),
        ("maximum", "google"),
    ]


def test_a_shorter_vendor_ladder_declares_its_collapse(payload: dict, anchors) -> None:
    tiers = generator.resolve_tiers(payload, anchors)
    for vendor in ("moonshotai", "google"):
        entry = tiers["maximum"]["vendors"][vendor]
        assert entry["collapse"]["same_model_as_tier"] == "high"
        assert entry["collapse"]["reason"].strip()
        assert entry["anchor"] == tiers["high"]["vendors"][vendor]["anchor"]
    for vendor in ("anthropic", "openai"):
        assert "collapse" not in tiers["maximum"]["vendors"][vendor], (
            f"{vendor} publishes a genuine fourth class, so its maximum must not collapse"
        )
        assert (
            tiers["maximum"]["vendors"][vendor]["anchor"]
            != (tiers["high"]["vendors"][vendor]["anchor"])
        )
    assert "collapse" not in tiers["high"]["vendors"]["openai"]


def test_a_collapse_that_disagrees_with_the_ids_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    moonshot = next(v for v in declared["vendors"] if v["id"] == "moonshotai")
    moonshot["tiers"]["maximum"] = "kimi-k2.6"
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "collapse" in str(excinfo.value) and "kimi-k2.6" in str(excinfo.value)


def test_a_collapse_without_a_reason_is_rejected(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "google").pop("collapse_reason")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "collapse_reason" in str(excinfo.value)


def test_provenance_stamps_the_digest_size_and_etag() -> None:
    raw = FIXTURE_PATH.read_bytes()
    stamp = generator.build_provenance(raw, '"abc123"')
    assert stamp["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert stamp["payload_bytes"] == len(raw)
    assert stamp["etag"] == '"abc123"'


def test_provenance_claims_no_commit_sha() -> None:
    stamp = generator.build_provenance(b"{}", None)
    assert not [key for key in stamp if "commit" in key]
    assert "etag" not in stamp, "an absent etag must be omitted, not stamped empty"


def test_provenance_records_the_upstream_url_never_a_local_path() -> None:
    stamp = generator.build_provenance(b"{}", None)
    assert stamp["source_url"] == generator.API_URL

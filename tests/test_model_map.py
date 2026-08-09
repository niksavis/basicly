"""Offline gates on the committed model map (basicly-kjc5.61).

The committed artifact half of the original suite, kept when the module-size
ratchet split it (basicly-u2hl.36): the map validates against its published
schema, covers exactly ``schema.MODEL_TIERS`` for every vendor, agrees with
``anchors.yaml``, and carries honest provenance. None of it touches the network,
which is why it can gate every commit while the drift check cannot.
"""

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
    """The captured models.dev document, parsed fresh so a mutation cannot leak."""
    return helpers.read_payload()


@pytest.fixture
def anchors():
    """The repo's real anchor source."""
    return helpers.read_anchors()


@pytest.fixture
def committed() -> dict[str, Any]:
    """The committed map."""
    return helpers.read_committed()


@pytest.fixture
def declared() -> dict[str, Any]:
    """The raw anchor source, as a reviewer reads it."""
    return helpers.read_declared()


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A models dir holding the real anchors and a map built from the fixture."""
    return helpers.make_workspace(tmp_path)


# --- the committed artifact (offline gates) ----------------------------------


def test_committed_map_validates_against_its_published_schema(committed: dict) -> None:
    """The map is a standalone artifact, so its own schema is the contract."""
    schema = json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))
    Draft202012Validator.check_schema(schema)
    errors = sorted(Draft202012Validator(schema).iter_errors(committed), key=str)
    assert not errors, [f"{list(e.absolute_path)}: {e.message}" for e in errors]


def test_committed_map_covers_every_tier_for_every_required_vendor(committed: dict) -> None:
    """The map is a tier x vendor reference; a missing cell is unresolvable."""
    assert committed["tier_order"] == list(MODEL_TIERS)
    assert tuple(committed["tiers"]) == MODEL_TIERS
    for tier in MODEL_TIERS:
        vendors = committed["tiers"][tier]["vendors"]
        for vendor in REQUIRED_VENDORS:
            assert vendor in vendors, f"tier '{tier}' has no '{vendor}' entry"


def test_every_vendor_is_resolved_on_its_own_surface_and_the_broker(committed: dict) -> None:
    """A vendor-native price is the baseline a broker's markup is read against."""
    for tier, vendor, _, _ in _cells(committed):
        surfaces = committed["tiers"][tier]["vendors"][vendor]["surfaces"]
        assert vendor in surfaces, f"{tier}/{vendor} is missing its own surface"
        assert BROKER_SURFACE in surfaces, f"{tier}/{vendor} is missing '{BROKER_SURFACE}'"


def test_committed_anchors_match_the_reviewed_anchor_source(
    committed: dict, declared: dict
) -> None:
    """An anchors.yaml edit with no regenerate must fail here, not go unnoticed."""
    for vendor in declared["vendors"]:
        for tier, anchor in vendor["tiers"].items():
            entry = committed["tiers"][tier]["vendors"][vendor["id"]]
            assert entry["anchor"] == anchor, f"{tier}/{vendor['id']} anchor drifted"


def test_committed_surface_table_matches_the_anchor_source(committed: dict, declared: dict) -> None:
    """Adding a surface without regenerating leaves it unresolved."""
    assert list(committed["surfaces"]) == list(declared["surfaces"])
    for vendor in declared["vendors"]:
        assert committed["vendors"][vendor["id"]]["surfaces"] == list(vendor["surfaces"])


def test_committed_map_is_exactly_what_the_generator_renders(committed: dict) -> None:
    """A hand-edit that reformats the generated file is caught by the byte compare."""
    assert generator.render(committed) == MAP_PATH.read_text(encoding="utf-8")


def test_the_fixture_reproduces_the_committed_tiers(committed: dict, anchors) -> None:
    """Proves the trimmed fixture is a faithful copy of the live document.

    Without this the hermetic tests below could all agree with a fixture that no
    longer resembles what models.dev actually serves.
    """
    built = generator.build_map(FIXTURE_PATH.read_bytes(), None, anchors)
    assert built["tiers"] == committed["tiers"]


# --- vendor coverage, collapse, availability ---------------------------------


def test_each_surface_spells_the_same_model_its_own_way(payload: dict, anchors) -> None:
    """The whole point: one anchor, two ids, because the providers disagree."""
    tiers = generator.resolve_tiers(payload, anchors)
    surfaces = tiers["low"]["vendors"]["anthropic"]["surfaces"]
    assert surfaces["anthropic"]["model"] == "claude-haiku-4-5"
    assert surfaces[BROKER_SURFACE]["model"] == "claude-haiku-4.5"


def test_cost_is_recorded_per_surface_not_per_vendor(payload: dict, anchors) -> None:
    """Measured: gpt-5.6-luna is 0.2/1.2 on openai and 1/6 on github-copilot."""
    tiers = generator.resolve_tiers(payload, anchors)
    low = tiers["low"]["vendors"]["openai"]["surfaces"]
    assert low["openai"]["cost_usd_per_mtok"] == {"input": 0.2, "output": 1.2}
    assert low[BROKER_SURFACE]["cost_usd_per_mtok"] == {"input": 1, "output": 6}

    medium = tiers["medium"]["vendors"]["openai"]["surfaces"]
    assert medium["openai"]["cost_usd_per_mtok"] == {"input": 2, "output": 12}
    assert medium[BROKER_SURFACE]["cost_usd_per_mtok"] == {"input": 2.5, "output": 15}


def test_an_unserved_tier_is_marked_unavailable_with_no_model_key(payload: dict, anchors) -> None:
    """Never substitute another tier's model — that is the silent demotion."""
    tiers = generator.resolve_tiers(payload, anchors)
    gap = tiers["low"]["vendors"]["moonshotai"]["surfaces"][BROKER_SURFACE]
    assert gap["status"] == "unavailable"
    assert "model" not in gap, "an unavailable cell must not carry a model id"
    assert "Kimi K2.5" in gap["reason"]

    served = tiers["medium"]["vendors"]["moonshotai"]["surfaces"][BROKER_SURFACE]
    assert served["status"] == "available"
    assert served["model"] == "kimi-k2.7-code"


def test_the_broker_gaps_are_exactly_the_measured_ones(payload: dict, anchors) -> None:
    """Pins the 2026-07-31 measurement: five of 32 cells have no model."""
    tiers = generator.resolve_tiers(payload, anchors)
    gaps = sorted(
        (tier, vendor)
        for tier, entry in tiers.items()
        for vendor, vendor_entry in entry["vendors"].items()
        if vendor_entry["surfaces"][BROKER_SURFACE]["status"] == "unavailable"
    )
    assert gaps == [
        ("high", "moonshotai"),
        ("low", "google"),
        ("low", "moonshotai"),
        ("maximum", "moonshotai"),
        ("medium", "google"),
    ]


def test_a_shorter_vendor_ladder_declares_its_collapse(payload: dict, anchors) -> None:
    """Three vendors collapse maximum onto high; Anthropic does not."""
    tiers = generator.resolve_tiers(payload, anchors)
    for vendor in ("openai", "moonshotai", "google"):
        entry = tiers["maximum"]["vendors"][vendor]
        assert entry["collapse"]["same_model_as_tier"] == "high"
        assert entry["collapse"]["reason"].strip()
        assert entry["anchor"] == tiers["high"]["vendors"][vendor]["anchor"]
    assert "collapse" not in tiers["maximum"]["vendors"]["anthropic"]
    assert "collapse" not in tiers["high"]["vendors"]["openai"]


def test_a_collapse_that_disagrees_with_the_ids_is_rejected(tmp_path: Path) -> None:
    """The declaration is cross-checked, so it cannot drift from the anchors."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    openai = next(v for v in declared["vendors"] if v["id"] == "openai")
    openai["tiers"]["maximum"] = "gpt-5.5-pro"
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "collapse" in str(excinfo.value) and "gpt-5.5-pro" in str(excinfo.value)


def test_a_collapse_without_a_reason_is_rejected(tmp_path: Path) -> None:
    """An unexplained collapse is indistinguishable from a duplicated row."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "google").pop("collapse_reason")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "collapse_reason" in str(excinfo.value)


# --- provenance --------------------------------------------------------------


def test_provenance_stamps_the_digest_size_and_etag() -> None:
    """The stamp identifies the exact bytes parsed, not a re-serialization."""
    raw = FIXTURE_PATH.read_bytes()
    stamp = generator.build_provenance(raw, '"abc123"')
    assert stamp["payload_sha256"] == hashlib.sha256(raw).hexdigest()
    assert stamp["payload_bytes"] == len(raw)
    assert stamp["etag"] == '"abc123"'


def test_provenance_claims_no_commit_sha() -> None:
    """models.dev publishes none, so no field may pretend otherwise."""
    stamp = generator.build_provenance(b"{}", None)
    assert not [key for key in stamp if "commit" in key]
    assert "etag" not in stamp, "an absent etag must be omitted, not stamped empty"


def test_provenance_records_the_upstream_url_never_a_local_path() -> None:
    """A committed artifact must carry no machine-specific path."""
    stamp = generator.build_provenance(b"{}", None)
    assert stamp["source_url"] == generator.API_URL

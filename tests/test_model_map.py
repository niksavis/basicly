"""Tests for the models.dev model-map generator and its drift check (basicly-kjc5.61).

Two halves. Offline gates on the *committed* artifact — it validates against its
published schema, covers exactly ``schema.MODEL_TIERS`` for every vendor, and
agrees with ``anchors.yaml`` — because the drift check needs the network and
therefore cannot run on every commit. And behavioural tests on the generator
itself, driven from a captured payload so no test touches the network.

Fixture provenance: ``tests/fixtures/modelsdev-api.json`` is a **real** response
from ``https://models.dev/api.json``, captured 2026-07-31 (HTTP 200, 3,331,578
bytes, 176 providers, 5911 model records) and trimmed to five providers. Every
record is byte-identical to upstream, and
``test_the_fixture_reproduces_the_committed_tiers`` proves the trim is faithful by
resolving it and comparing against the map generated from the full live document.

The trim deliberately keeps the parts a hand-written fixture would have smoothed
away:

* Anthropic's ``claude-haiku-4-5-20251001`` — the second record named "Claude
  Haiku 4.5", which is why the " (latest)" suffix must stay in the join key.
* ``github-copilot``'s ``claude-haiku-4.5`` and ``claude-opus-4.5``, which carry
  ``limit.input``, next to its ``claude-sonnet-5``, which does not.
* ``github-copilot``'s real coverage gaps: its only Moonshot model
  (``kimi-k2.7-code``) and its whole four-model Gemini range, so
  ``kimi-k2.5``/``kimi-k3``/``gemini-3.1-flash-lite``/``gemini-3.6-flash``
  resolve to genuinely unavailable rather than to a synthesized gap.
* Non-general noise a sweep would happily pick as a tier: ``gpt-image-2``,
  ``gpt-realtime-2.1``, ``text-embedding-3-small``, ``gemini-3.1-flash-image``,
  ``gemini-3.1-flash-tts-preview``, ``gemini-embedding-2``,
  ``veo-3.1-generate-preview``.
* ``gpt-5.5-pro``, a model the anchors deliberately do not use.
"""

from __future__ import annotations

import copy
import hashlib
import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest
import yaml
from jsonschema import Draft202012Validator

from basicly.schema import MODEL_TIERS

REPO = Path(__file__).resolve().parents[1]
MODELS_DIR = REPO / ".basicly" / "core" / "models"
ANCHORS_PATH = MODELS_DIR / "anchors.yaml"
MAP_PATH = MODELS_DIR / "model-map.json"
SCHEMA_PATH = MODELS_DIR / "model-map.schema.json"
FIXTURE_PATH = Path(__file__).parent / "fixtures" / "modelsdev-api.json"

# The vendors the map is required to cover, and the broker surface every one of
# them is also resolved onto. Spelled out rather than read from anchors.yaml: an
# assertion derived from its subject cannot fail when the subject shrinks.
REQUIRED_VENDORS = ("anthropic", "openai", "moonshotai", "google")
BROKER_SURFACE = "github-copilot"


def _load_module():
    """Load the generate-model-map script module from its path.

    Registered in ``sys.modules`` before execution because ``@dataclass``
    resolves its defining module by name.
    """
    script_path = REPO / ".scripts" / "generate_model_map.py"
    spec = importlib.util.spec_from_file_location("generate_model_map", script_path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


generator = _load_module()


@pytest.fixture
def payload() -> dict[str, Any]:
    """The captured models.dev document, parsed fresh so a mutation cannot leak."""
    return json.loads(FIXTURE_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def anchors():
    """The repo's real anchor source — the thing the map is generated from."""
    return generator.load_anchors(ANCHORS_PATH)


@pytest.fixture
def committed() -> dict[str, Any]:
    """The committed map."""
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def declared() -> dict[str, Any]:
    """The raw anchor source, as a reviewer reads it."""
    return yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))


@pytest.fixture
def workspace(tmp_path: Path) -> Path:
    """A models dir holding the real anchors and a map built from the fixture."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "anchors.yaml").write_text(
        ANCHORS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )
    built = generator.build_map(
        FIXTURE_PATH.read_bytes(), None, generator.load_anchors(ANCHORS_PATH)
    )
    (models_dir / "model-map.json").write_text(generator.render(built), encoding="utf-8")
    return models_dir


def _run(models_dir: Path, *extra: str) -> int:
    """Invoke the script's entry point against a workspace and the fixture."""
    return generator.main([
        "--models-dir",
        str(models_dir),
        "--payload",
        str(FIXTURE_PATH),
        *extra,
    ])


def _cells(document: dict[str, Any]):
    """Yield every (tier, vendor, surface, entry) cell of a map."""
    for tier, entry in document["tiers"].items():
        for vendor, vendor_entry in entry["vendors"].items():
            for surface, served in vendor_entry["surfaces"].items():
                yield tier, vendor, surface, served


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


# --- the general-model rule --------------------------------------------------


def test_the_rule_refuses_a_non_general_anchor(tmp_path: Path, payload: dict) -> None:
    """A sweep would happily pick an image model; the rule names why it cannot."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "google")["tiers"]["low"] = (
        "gemini-3.1-flash-image"
    )
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.resolve_tiers(payload, generator.load_anchors(path))
    message = str(excinfo.value)
    assert "gemini-3.1-flash-image" in message
    assert "tool" in message or "text only" in message


@pytest.mark.parametrize(
    ("provider", "model_id"),
    [
        ("openai", "gpt-image-2"),
        ("openai", "gpt-realtime-2.1"),
        ("openai", "text-embedding-3-small"),
        ("google", "gemini-3.1-flash-image"),
        ("google", "gemini-3.1-flash-tts-preview"),
        ("google", "gemini-embedding-2"),
        ("google", "veo-3.1-generate-preview"),
    ],
)
def test_the_rule_excludes_every_non_general_model_in_the_fixture(
    payload: dict, anchors, provider: str, model_id: str
) -> None:
    """Real upstream records, not invented ones: each must fail the rule."""
    record = payload[provider]["models"][model_id]
    assert anchors.rule.failures(record), f"{provider}/{model_id} wrongly passes the rule"


def test_the_rule_admits_every_committed_anchor(payload: dict, anchors) -> None:
    """The complement of the exclusion test, so the rule is not merely strict."""
    for vendor in anchors.vendors:
        for tier, anchor in vendor.tiers.items():
            record = payload[vendor.id]["models"][anchor]
            assert not anchors.rule.failures(record), f"{tier}/{vendor.id} fails the rule"


def test_reasoning_is_recorded_but_not_required(payload: dict, anchors) -> None:
    """Requiring it would exclude general tool-calling models such as gpt-4o."""
    assert anchors.rule.failures(payload["moonshotai"]["models"]["kimi-k2-turbo-preview"]) == []
    tiers = generator.resolve_tiers(payload, anchors)
    assert tiers["high"]["vendors"]["google"]["reasoning"] is True


# --- resolution mechanics ----------------------------------------------------


def test_resolution_keeps_the_latest_suffix_in_the_join_key(payload: dict, anchors) -> None:
    """Stripping " (latest)" makes the low anchor match two Anthropic records."""
    tiers = generator.resolve_tiers(payload, anchors)
    entry = tiers["low"]["vendors"]["anthropic"]
    assert entry["model_name"] == "Claude Haiku 4.5 (latest)"
    assert entry["surfaces"]["anthropic"]["model"] == "claude-haiku-4-5"
    dated = payload["anthropic"]["models"]["claude-haiku-4-5-20251001"]
    assert dated["name"] == "Claude Haiku 4.5", "fixture must keep the near-miss record"


def test_resolution_treats_the_provider_input_limit_as_optional(payload: dict, anchors) -> None:
    """Only some providers publish limit.input, and not for every model."""
    tiers = generator.resolve_tiers(payload, anchors)
    low = tiers["low"]["vendors"]["anthropic"]["surfaces"]
    assert low[BROKER_SURFACE]["limit_tokens"]["input"] == 136000
    assert "input" not in low["anthropic"]["limit_tokens"]
    medium = tiers["medium"]["vendors"]["anthropic"]["surfaces"]
    assert "input" not in medium[BROKER_SURFACE]["limit_tokens"]


def test_resolution_fails_naming_an_anchor_that_no_longer_resolves(payload: dict, anchors) -> None:
    """An anchor id pulled upstream must name itself, not vanish silently."""
    del payload["google"]["models"]["gemini-3.1-pro-preview"]
    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.resolve_tiers(payload, anchors)
    message = str(excinfo.value)
    assert "gemini-3.1-pro-preview" in message
    assert "high" in message


def test_resolution_rejects_an_ambiguous_name_match(payload: dict, anchors) -> None:
    """Two records sharing the join key is a guess, so it is an error."""
    models = payload[BROKER_SURFACE]["models"]
    models["claude-haiku-4.5-alias"] = copy.deepcopy(models["claude-haiku-4.5"])
    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.resolve_tiers(payload, anchors)
    assert "claude-haiku-4.5-alias" in str(excinfo.value)


def test_resolution_rejects_a_non_numeric_upstream_cost(payload: dict, anchors) -> None:
    """Upstream is community-contributed, so its numbers are validated on arrival."""
    payload["anthropic"]["models"]["claude-sonnet-5"]["cost"]["input"] = "two dollars"
    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.resolve_tiers(payload, anchors)
    assert "cost" in str(excinfo.value) and "claude-sonnet-5" in str(excinfo.value)


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


# --- drift check -------------------------------------------------------------


def test_check_passes_when_the_map_matches_upstream(workspace: Path) -> None:
    """The green path, so the failing paths below are not vacuous."""
    assert _run(workspace, "--check") == 0


def test_check_fails_naming_the_id_and_the_change_when_a_cost_moves(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The dated tripwire: medium reverts to 3 dollars per MTok on 2026-09-01."""
    map_path = workspace / "model-map.json"
    document = json.loads(map_path.read_text(encoding="utf-8"))
    cell = document["tiers"]["medium"]["vendors"]["anthropic"]["surfaces"]["anthropic"]
    cell["cost_usd_per_mtok"]["input"] = 3
    map_path.write_text(generator.render(document), encoding="utf-8")

    assert _run(workspace, "--check") == 1
    err = capsys.readouterr().err
    assert "medium.vendors.anthropic.surfaces.anthropic.cost_usd_per_mtok.input" in err
    assert "3 -> 2" in err
    assert "anthropic/claude-sonnet-5" in err


def test_check_reports_a_surface_that_stopped_serving_a_tier(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A cell flipping available to unavailable is the silent-demotion tripwire."""
    map_path = workspace / "model-map.json"
    document = json.loads(map_path.read_text(encoding="utf-8"))
    document["tiers"]["low"]["vendors"]["moonshotai"]["surfaces"][BROKER_SURFACE] = {
        "status": "available",
        "model": "kimi-k2.5",
        "cost_usd_per_mtok": {"input": 0.6, "output": 3},
        "limit_tokens": {"context": 262144, "output": 262144},
        "upstream_last_updated": "2026-01-01",
    }
    map_path.write_text(generator.render(document), encoding="utf-8")

    assert _run(workspace, "--check") == 1
    err = capsys.readouterr().err
    assert "low.vendors.moonshotai.surfaces.github-copilot.status" in err
    assert "'available' -> 'unavailable'" in err


def test_check_does_not_mutate_the_committed_map(workspace: Path) -> None:
    """Reporting, never auto-applying: a bad upstream edit must not land silently."""
    map_path = workspace / "model-map.json"
    document = json.loads(map_path.read_text(encoding="utf-8"))
    document["tiers"]["low"]["vendors"]["anthropic"]["surfaces"][BROKER_SURFACE]["model"] = (
        "claude-haiku-9.9"
    )
    before = generator.render(document)
    map_path.write_text(before, encoding="utf-8")

    assert _run(workspace, "--check") == 1
    assert map_path.read_text(encoding="utf-8") == before


def test_check_names_a_changed_model_id(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A re-spelled serving id is drift and both spellings are reported."""
    map_path = workspace / "model-map.json"
    document = json.loads(map_path.read_text(encoding="utf-8"))
    document["tiers"]["low"]["vendors"]["anthropic"]["surfaces"][BROKER_SURFACE]["model"] = (
        "claude-haiku-9.9"
    )
    map_path.write_text(generator.render(document), encoding="utf-8")

    assert _run(workspace, "--check") == 1
    assert "'claude-haiku-9.9' -> 'claude-haiku-4.5'" in capsys.readouterr().err


def test_check_ignores_provenance_drift(workspace: Path) -> None:
    """The shared upstream document changes daily; only the tiers matter."""
    map_path = workspace / "model-map.json"
    document = json.loads(map_path.read_text(encoding="utf-8"))
    document["provenance"]["payload_sha256"] = "0" * 64
    document["provenance"]["fetched_date"] = "2000-01-01"
    document["provenance"]["payload_bytes"] = 1
    map_path.write_text(generator.render(document), encoding="utf-8")

    assert _run(workspace, "--check") == 0


def test_check_fails_and_writes_nothing_when_an_anchor_is_unresolvable(
    workspace: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """A pinned id that no longer resolves is named, and the map stays put."""
    anchors_path = workspace / "anchors.yaml"
    anchors_path.write_text(
        anchors_path.read_text(encoding="utf-8").replace("kimi-k2.5", "kimi-k2.9"),
        encoding="utf-8",
    )
    before = (workspace / "model-map.json").read_text(encoding="utf-8")

    assert _run(workspace, "--check") == 1
    assert "kimi-k2.9" in capsys.readouterr().err
    assert (workspace / "model-map.json").read_text(encoding="utf-8") == before


def test_writing_creates_the_map_and_the_check_then_passes(tmp_path: Path) -> None:
    """The generate path, exercised through the entry point."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "anchors.yaml").write_text(
        ANCHORS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert _run(models_dir) == 0
    written = json.loads((models_dir / "model-map.json").read_text(encoding="utf-8"))
    assert tuple(written["tiers"]) == MODEL_TIERS
    assert _run(models_dir, "--check") == 0


def test_check_reports_a_missing_map_instead_of_writing_one(tmp_path: Path) -> None:
    """--check never creates the artifact it is checking."""
    models_dir = tmp_path / "models"
    models_dir.mkdir()
    (models_dir / "anchors.yaml").write_text(
        ANCHORS_PATH.read_text(encoding="utf-8"), encoding="utf-8"
    )

    assert _run(models_dir, "--check") == 1
    assert not (models_dir / "model-map.json").exists()


# --- anchor validation -------------------------------------------------------


def test_every_vendor_must_declare_exactly_the_tier_vocabulary(tmp_path: Path) -> None:
    """The vocabulary has one definition; anchors.yaml may not extend or shrink it."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "moonshotai")["tiers"].pop("maximum")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "maximum" in str(excinfo.value) and "moonshotai" in str(excinfo.value)


def test_a_vendor_must_list_its_own_surface(tmp_path: Path) -> None:
    """Without the native price there is no baseline for a broker's markup."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    vendor = next(v for v in declared["vendors"] if v["id"] == "openai")
    vendor["surfaces"] = [BROKER_SURFACE]
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "own id" in str(excinfo.value)


def test_a_vendor_cannot_reference_an_undeclared_surface(tmp_path: Path) -> None:
    """A typo'd surface would otherwise resolve to a missing provider at fetch time."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "google")["surfaces"].append("gooogle")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "gooogle" in str(excinfo.value)


def test_anchors_reject_a_duplicate_vendor_id(tmp_path: Path) -> None:
    """Two vendors with one id would silently drop a whole column."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    declared["vendors"].append(dict(declared["vendors"][0]))
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "duplicate vendor id" in str(excinfo.value)


def test_anchors_reject_an_unsupported_schema_version(tmp_path: Path) -> None:
    """A source authored for a newer generator fails loudly, not by misreading."""
    path = tmp_path / "anchors.yaml"
    path.write_text(
        ANCHORS_PATH.read_text(encoding="utf-8").replace("schema_version: 2", "schema_version: 3"),
        encoding="utf-8",
    )

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "schema_version" in str(excinfo.value)


def test_anchors_require_an_explicit_general_model_rule(tmp_path: Path) -> None:
    """The capability floor must be stated, not defaulted into existence."""
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    declared["general_model_rule"].pop("require_tool_call")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "require_tool_call" in str(excinfo.value)

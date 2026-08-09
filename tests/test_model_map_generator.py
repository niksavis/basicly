"""The generator's behaviour, driven from a captured payload.

Split from the original suite by the module-size ratchet (basicly-u2hl.36).
Everything here exercises code rather than the committed artifact: the
general-model rule that keeps a sweep from picking an image or embedding model as
a tier, the resolution mechanics that turn an anchor into a per-surface cell, and
the drift check that reports an upstream change without ever writing it.
"""

from __future__ import annotations

import copy
import json
from pathlib import Path
from typing import Any

import pytest
import yaml

from basicly.schema import MODEL_TIERS
from tests import model_map_helpers as helpers
from tests.model_map_helpers import (
    ANCHORS_PATH,
    BROKER_SURFACE,
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


def test_cost_is_read_per_surface_not_per_vendor(payload: dict, anchors) -> None:
    """A cell takes its price from the surface serving it, not from the vendor.

    Moved here from the committed-artifact suite on 2026-08-09 (basicly-u2hl.39).
    It used to be evidenced by a real divergence — ``gpt-5.6-luna`` at 0.2/1.2 on
    openai against 1/6 on the broker — and upstream has since normalised broker
    pricing to native on every matched model, so the fixture can no longer
    discriminate. The mechanism is unchanged and this drives it from a payload
    where the two differ, which is the only way it can still fail.
    """
    broker = payload[BROKER_SURFACE]["models"]["gpt-5.6-luna"]
    native = payload["openai"]["models"]["gpt-5.6-luna"]
    priced = ("input", "output")  # the only two keys a cell carries; native also has cache_write
    assert {k: native["cost"][k] for k in priced} == {k: broker["cost"][k] for k in priced}, (
        "precondition: the surfaces agree before the edit, so the assertion below "
        "discriminates the edit rather than a difference that was already there"
    )
    broker["cost"] = {"input": 1, "output": 6}

    surfaces = generator.resolve_tiers(payload, anchors)["low"]["vendors"]["openai"]["surfaces"]

    assert surfaces["openai"]["cost_usd_per_mtok"] == {"input": 0.2, "output": 1.2}
    assert surfaces[BROKER_SURFACE]["cost_usd_per_mtok"] == {"input": 1, "output": 6}


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

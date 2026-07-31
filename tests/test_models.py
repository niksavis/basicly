"""Tier resolution against the committed model map (basicly-kjc5.59)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import models
from basicly.schema import MODEL_TIERS


def _map(**cells: object) -> dict:
    """A minimal map with one vendor and one surface per given tier."""
    return {"tiers": dict(cells)}


def _available(model: str) -> dict:
    return {
        "vendors": {
            "anthropic": {"surfaces": {"anthropic": {"status": "available", "model": model}}}
        }
    }


def test_the_committed_map_resolves_every_tier_the_vocabulary_declares() -> None:
    """The shipped map must answer for all four tiers on the repo's own surfaces.

    A positive control for the whole mechanism: if this ever goes quiet because
    the map lost a tier, every tier-declaring dispatch would start refusing and
    the refusal would look like a config error rather than missing data.
    """
    mapping = models.load_map(Path())
    for tier in MODEL_TIERS:
        assert models.model_for(tier, "anthropic", "anthropic", mapping=mapping)
        assert models.model_for(tier, "anthropic", "github-copilot", mapping=mapping)


def test_a_surface_decides_the_spelling() -> None:
    """One model, two surface spellings — the reason a pinned id is not portable."""
    mapping = models.load_map(Path())
    assert models.model_for("low", "anthropic", "anthropic", mapping=mapping) == "claude-haiku-4-5"
    assert (
        models.model_for("low", "anthropic", "github-copilot", mapping=mapping)
        == "claude-haiku-4.5"
    )


def test_an_unavailable_cell_refuses_and_carries_the_maps_reason() -> None:
    """Never a substitution — the silent demotion basicly-izda exists to prevent."""
    mapping = models.load_map(Path())
    with pytest.raises(models.ModelUnavailableError) as excinfo:
        models.model_for("low", "moonshotai", "github-copilot", mapping=mapping)
    assert "unavailable" in str(excinfo.value)
    # The map's own reason reaches the caller rather than a generic "not found".
    assert "serves no model" in str(excinfo.value)


@pytest.mark.parametrize(
    "tier,vendor,surface",
    [
        ("bogus", "anthropic", "anthropic"),
        ("low", "nobody", "anthropic"),
        ("low", "anthropic", "nowhere"),
    ],
)
def test_an_unknown_coordinate_refuses(tier: str, vendor: str, surface: str) -> None:
    """Each axis refuses on its own; none of them falls through to a default."""
    with pytest.raises(models.ModelUnavailableError):
        models.model_for(tier, vendor, surface, mapping=models.load_map(Path()))


def test_a_cell_marked_unavailable_is_refused_even_if_it_carries_a_model() -> None:
    """Status wins over the presence of a key, so a stale id cannot leak out."""
    mapping = _map(low=_available("should-not-be-used"))
    cell = mapping["tiers"]["low"]["vendors"]["anthropic"]["surfaces"]["anthropic"]
    cell["status"] = "unavailable"
    with pytest.raises(models.ModelUnavailableError):
        models.model_for("low", "anthropic", "anthropic", mapping=mapping)


def test_a_missing_map_is_an_error_not_an_empty_map(tmp_path: Path) -> None:
    """A broken map must not read as "every tier unavailable" — different claims."""
    with pytest.raises(models.ModelMapError):
        models.load_map_from(tmp_path / "absent.json")


def test_a_malformed_map_is_an_error(tmp_path: Path) -> None:
    """Unparseable data must raise, not degrade to a map with no tiers in it."""
    path = tmp_path / "model-map.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(models.ModelMapError):
        models.load_map_from(path)


def test_a_map_without_tiers_is_an_error(tmp_path: Path) -> None:
    """Valid JSON that is not a map is still unusable, and says so."""
    path = tmp_path / "model-map.json"
    path.write_text(json.dumps({"schema_version": 2}), encoding="utf-8")
    with pytest.raises(models.ModelMapError):
        models.load_map_from(path)


def test_a_repo_local_map_wins_over_the_bundled_copy(tmp_path: Path) -> None:
    """A consumer's own map takes precedence over the copy inside the wheel.

    `basicly install` materializes the map into a consumer repo, and that copy is
    the one the consumer's own gates reviewed.
    """
    local = tmp_path / models.LOCAL_CATALOG_DIR / models.MODELS_DIRNAME
    local.mkdir(parents=True)
    (local / models.MAP_FILENAME).write_text(
        json.dumps(_map(low=_available("consumer-pinned-model"))), encoding="utf-8"
    )
    assert models.map_path(tmp_path) == local / models.MAP_FILENAME
    assert models.model_for("low", "anthropic", "anthropic", repo_root=tmp_path) == (
        "consumer-pinned-model"
    )


# --- same_model: the spellings measured on the real CLIs 2026-07-31 -----------


@pytest.mark.parametrize(
    "pinned,observed",
    [
        # Exactly what claude 2.1.220 reported for a `--model haiku` dispatch: the
        # modelUsage key is the dated build while the pin and the map say the
        # short id, so literal equality would flag every healthy run.
        ("claude-haiku-4-5", "claude-haiku-4-5-20251001"),
        # A bare capability alias is a legal pin the CLI resolves for us.
        ("haiku", "claude-haiku-4-5-20251001"),
        # The two surface spellings of one model (anthropic vs github-copilot).
        ("claude-haiku-4-5", "claude-haiku-4.5"),
        ("claude-haiku-4-5", "claude-haiku-4-5"),
    ],
)
def test_same_model_tolerates_a_surface_spelling_or_a_dated_build(
    pinned: str, observed: str
) -> None:
    """One model under two spellings is one model, or every run reads as a mismatch."""
    assert models.same_model(pinned, observed)


@pytest.mark.parametrize(
    "pinned,observed",
    [
        ("claude-opus-5", "claude-sonnet-5"),
        ("claude-opus-5", "claude-haiku-4-5"),
        ("gpt-5.6-terra", "claude-opus-5"),
        # A near-miss inside one family is still a different model.
        ("claude-haiku-4-5", "claude-haiku-3-5"),
    ],
)
def test_same_model_still_reports_a_genuinely_different_model(pinned: str, observed: str) -> None:
    """The tolerance must not swallow a real divergence.

    That mismatch record is the only signal an injected tier silently did not take.
    """
    assert not models.same_model(pinned, observed)

"""Validation of the reviewed anchor source (basicly-u2hl.36).

``anchors.yaml`` is hand-authored input, so every way a human can get it wrong is
a case here: an unsupported schema version, a missing tier, a duplicate vendor, a
collapse that does not agree with the ids it names, and a vendor walk order that
is not a permutation of the declared vendors. The map's own shape is gated in the
sibling artifact suite.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
import yaml

from tests import model_map_helpers as helpers
from tests.model_map_helpers import (
    ANCHORS_PATH,
    BROKER_SURFACE,
    anchors_module,
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
    # Derived from the constant rather than written as a literal: pinning "2 -> 3"
    # here made this test silently stop testing the moment the format bumped to 3,
    # because the substitution no longer matched and the unchanged source loaded
    # cleanly. The assert below is the control that the edit actually happened.
    current = anchors_module.ANCHORS_SCHEMA_VERSION
    source = ANCHORS_PATH.read_text(encoding="utf-8")
    newer = source.replace(f"schema_version: {current}", f"schema_version: {current + 1}")
    assert newer != source, f"no 'schema_version: {current}' line to bump in {ANCHORS_PATH}"
    path.write_text(newer, encoding="utf-8")

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

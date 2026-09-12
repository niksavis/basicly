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


def test_every_vendor_must_declare_exactly_the_tier_vocabulary(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "moonshotai")["tiers"].pop("maximum")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "maximum" in str(excinfo.value) and "moonshotai" in str(excinfo.value)


def test_a_vendor_must_list_its_own_surface(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    vendor = next(v for v in declared["vendors"] if v["id"] == "openai")
    vendor["surfaces"] = [BROKER_SURFACE]
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "own id" in str(excinfo.value)


def test_a_vendor_cannot_reference_an_undeclared_surface(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    next(v for v in declared["vendors"] if v["id"] == "google")["surfaces"].append("gooogle")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "gooogle" in str(excinfo.value)


def test_anchors_reject_a_duplicate_vendor_id(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    declared["vendors"].append(dict(declared["vendors"][0]))
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "duplicate vendor id" in str(excinfo.value)


def test_anchors_reject_an_unsupported_schema_version(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    current = anchors_module.ANCHORS_SCHEMA_VERSION
    source = ANCHORS_PATH.read_text(encoding="utf-8")
    newer = source.replace(f"schema_version: {current}", f"schema_version: {current + 1}")
    assert newer != source, f"no 'schema_version: {current}' line to bump in {ANCHORS_PATH}"
    path.write_text(newer, encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "schema_version" in str(excinfo.value)


def test_anchors_require_an_explicit_general_model_rule(tmp_path: Path) -> None:
    path = tmp_path / "anchors.yaml"
    declared = yaml.safe_load(ANCHORS_PATH.read_text(encoding="utf-8"))
    declared["general_model_rule"].pop("require_tool_call")
    path.write_text(yaml.safe_dump(declared), encoding="utf-8")

    with pytest.raises(generator.ResolutionError) as excinfo:
        generator.load_anchors(path)
    assert "require_tool_call" in str(excinfo.value)

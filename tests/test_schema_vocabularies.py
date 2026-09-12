from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from basicly.schema import CATEGORIES, MODEL_TIERS, PRIORITY_MAP, STATUSES
from basicly.skill_source import INVOCATIONS

if TYPE_CHECKING:
    from collections.abc import Iterable

REPO = Path(__file__).parent.parent

SCHEMA_ENUMS = [
    ("fragment", "category", CATEGORIES),
    ("fragment", "priority", PRIORITY_MAP),
    ("fragment", "status", STATUSES),
    ("skill", "invocation", INVOCATIONS),
    ("agent", "tier", MODEL_TIERS),
]


def _schema(kind: str) -> dict:
    path = REPO / f".basicly/core/schemas/{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("kind", "prop", "vocabulary"), SCHEMA_ENUMS)
def test_a_schema_enum_matches_its_python_vocabulary(
    kind: str, prop: str, vocabulary: Iterable[str]
) -> None:
    assert set(_schema(kind)["properties"][prop]["enum"]) == set(vocabulary)


def test_the_agent_schema_tier_enum_keeps_the_model_tier_order() -> None:
    assert _schema("agent")["properties"]["tier"]["enum"] == list(MODEL_TIERS)

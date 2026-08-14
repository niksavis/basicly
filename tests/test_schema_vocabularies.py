"""Every catalog JSON Schema enum agrees with the Python vocabulary it restates.

The boundary is vocabulary agreement against ``test_catalog_lint``, which owns the
lint diagnostics themselves. A schema enum and its Python constant are two
declarations of one list: only core sources are validated against the schema, so a
value added to one alone makes core sources fail lint while overlays keep passing.
That split is what produced basicly-axqe for tiers.

``source`` (``core``/``user``) is restated by fragment.schema.json and
agent.schema.json but has no Python constant to bind to - it is an inline set
literal at ``loader.py:256`` - so it is absent from the table below by necessity.
"""

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

# Both sides are read from their real source. A hard-coded list on each side would
# be a third copy of the vocabulary rather than a check on the first two.
SCHEMA_ENUMS = [
    ("fragment", "category", CATEGORIES),
    ("fragment", "priority", PRIORITY_MAP),
    ("fragment", "status", STATUSES),
    ("skill", "invocation", INVOCATIONS),
    ("agent", "tier", MODEL_TIERS),
]


def _schema(kind: str) -> dict:
    """The shipped catalog JSON Schema for one source kind."""
    path = REPO / f".basicly/core/schemas/{kind}.schema.json"
    return json.loads(path.read_text(encoding="utf-8"))


@pytest.mark.parametrize(("kind", "prop", "vocabulary"), SCHEMA_ENUMS)
def test_a_schema_enum_matches_its_python_vocabulary(
    kind: str, prop: str, vocabulary: Iterable[str]
) -> None:
    """A newly restated enum belongs in SCHEMA_ENUMS above, not in a test of its own."""
    assert set(_schema(kind)["properties"][prop]["enum"]) == set(vocabulary)


def test_the_agent_schema_tier_enum_keeps_the_model_tier_order() -> None:
    """MODEL_TIERS is ordered cheapest first, which the set comparison above cannot see."""
    assert _schema("agent")["properties"]["tier"]["enum"] == list(MODEL_TIERS)

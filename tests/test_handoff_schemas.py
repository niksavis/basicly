"""Every handoff artifact kind the requirements name has a usable schema (basicly-r4jm).

The claim under test is not "the JSON parses". It is that a schema **refuses** what it
should: four of the seven loop roles were authored against artifacts that did not exist,
so a schema that accepted anything would leave them exactly as unreachable while looking
built.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from basicly import integrity

SCHEMA_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "schemas"

# The seven the requirements name (factory-loop §8). Two shipped earlier; this bead
# added the rest. Listed rather than globbed: a glob would pass by finding nothing.
HANDOFF_KINDS = (
    "implementation-plan",
    "change-summary",
    "classification",
    "change-shape",
    "verification-evidence",
    "validation-transcript",
    "release-record",
)


def _schema(kind: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_named_kind_has_a_schema_file_named_for_it(kind: str) -> None:
    """Resolution is by filename, so the name is the wiring (`catalog_source`)."""
    assert (SCHEMA_DIR / f"{kind}.schema.json").is_file()


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_schema_is_strict_about_what_it_admits(kind: str) -> None:
    """A permissive schema is the failure this bead exists to avoid.

    `additionalProperties: false` at the top level and a `required` naming every
    declared property are what make a malformed handoff a refusal rather than a
    silently thin artifact the next state reasons from.
    """
    schema = _schema(kind)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert set(schema["required"]) == set(schema["properties"])
    assert schema["properties"]["schema_version"]["const"] == 1


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_schema_refuses_a_payload_missing_a_required_field(kind: str) -> None:
    """The discrimination check: drop one required key and the schema must object."""
    schema = _schema(kind)
    dropped = sorted(schema["required"])[0]
    payload = {key: "x" for key in schema["required"] if key != dropped}
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert any(dropped in error.message for error in errors)


def test_the_classification_schema_accepts_what_the_engine_actually_computes() -> None:
    """Built from `integrity.assign`, not hand-written.

    A schema agreeing with an example someone wrote for it proves nothing; the payload
    that has to validate is the one the engine already produces, which `classify` has
    been writing as a `[harness-classification]` marker since before the artifact
    existed.
    """
    assignment = integrity.assign(("src/basicly/cli.py", "tests/test_cli.py"))
    selects = assignment.selection
    payload = {
        "schema_version": 1,
        "issue": "basicly-r4jm",
        "level": assignment.level,
        "depth": "build",
        "rule": assignment.rule,
        "reason": assignment.reason,
        "selects": {
            "gates": list(selects.gates),
            "model_tier": selects.model_tier,
            "rework_allowance": selects.rework_allowance,
            "ship": selects.ship,
        },
    }
    assert list(Draft202012Validator(_schema("classification")).iter_errors(payload)) == []


def test_the_classification_schema_admits_every_level_the_rule_can_assign() -> None:
    """A schema that only fits L3 would refuse most of the tree's own work."""
    validator = Draft202012Validator(_schema("classification"))
    for scope in (("docs/x.md",), ("src/basicly/policy.py",), ("src/basicly/cli.py",)):
        assignment = integrity.assign(scope)
        selects = assignment.selection
        payload = {
            "schema_version": 1,
            "issue": "i",
            "level": assignment.level,
            "depth": "build",
            "rule": assignment.rule,
            "reason": assignment.reason,
            "selects": {
                "gates": list(selects.gates),
                "model_tier": selects.model_tier,
                "rework_allowance": selects.rework_allowance,
                "ship": selects.ship,
            },
        }
        assert list(validator.iter_errors(payload)) == [], assignment.level

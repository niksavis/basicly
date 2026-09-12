from __future__ import annotations

import json
from pathlib import Path

import pytest
from jsonschema import Draft202012Validator

from basicly import handoff, integrity

SCHEMA_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "schemas"

KINDS_WITHOUT_A_SCHEMA = frozenset({"solution-design"})

HANDOFF_KINDS = tuple(kind for kind in handoff.PRODUCERS if kind not in KINDS_WITHOUT_A_SCHEMA)


def _declared_with_no_schema_file() -> set[str]:

    return {
        kind for kind in handoff.PRODUCERS if not (SCHEMA_DIR / f"{kind}.schema.json").is_file()
    }


def _drift() -> set[str]:

    return _declared_with_no_schema_file() ^ set(KINDS_WITHOUT_A_SCHEMA)


def test_the_kinds_this_suite_exercises_are_one_enumeration_with_the_declaration() -> None:

    assert set(HANDOFF_KINDS) | KINDS_WITHOUT_A_SCHEMA == set(handoff.PRODUCERS)
    assert _drift() == set()


def test_a_kind_the_declaration_gains_is_named_here_rather_than_silently_unexercised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    monkeypatch.setitem(handoff.PRODUCERS, "a-ninth-kind", None)
    assert _drift() == {"a-ninth-kind"}


def _schema(kind: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_named_kind_has_a_schema_file_named_for_it(kind: str) -> None:
    assert (SCHEMA_DIR / f"{kind}.schema.json").is_file()


def _demanded(schema: dict) -> set[str]:

    branches = (schema, schema.get("if", {}), schema.get("else", {}))
    return {name for branch in branches for name in branch.get("required", ())}


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_schema_is_strict_about_what_it_admits(kind: str) -> None:

    schema = _schema(kind)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert _demanded(schema) == set(schema["properties"])
    assert schema["properties"]["schema_version"]["const"] == 1


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_schema_refuses_a_payload_missing_a_required_field(kind: str) -> None:
    schema = _schema(kind)
    dropped = sorted(schema["required"])[0]
    payload = {key: "x" for key in schema["required"] if key != dropped}
    errors = list(Draft202012Validator(schema).iter_errors(payload))
    assert any(dropped in error.message for error in errors)


def test_the_classification_schema_accepts_what_the_engine_actually_computes() -> None:

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

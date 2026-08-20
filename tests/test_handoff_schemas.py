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

from basicly import handoff, integrity

SCHEMA_DIR = Path(__file__).parent.parent / ".basicly" / "core" / "schemas"

# The one kind `handoff.PRODUCERS` declares that no schema is authored for (basicly-qnt8ng).
# `basicly-u2hl.59` added `PRODUCERS` so which kinds exist stops being read out of absence, and
# a second hand maintained copy of that set here put the same drift back one layer out: a ninth
# kind could enter one list and not the other, and the one it missed is the one that exercises
# it. Not a glob over the schema directory, which would pass by finding nothing — the
# declaration is the source, and `test_handoff` already pins it against its own producers.
KINDS_WITHOUT_A_SCHEMA = frozenset({"solution-design"})

# The kinds the requirements name (factory-loop §8) that a schema is authored for.
HANDOFF_KINDS = tuple(kind for kind in handoff.PRODUCERS if kind not in KINDS_WITHOUT_A_SCHEMA)


def _declared_with_no_schema_file() -> set[str]:
    """Every declared kind that no schema file on disk is named for.

    Measured off disk rather than read off :data:`KINDS_WITHOUT_A_SCHEMA`, which is what makes
    the assertion discriminate: the constant is the claim and this is the observation.
    """
    return {
        kind for kind in handoff.PRODUCERS if not (SCHEMA_DIR / f"{kind}.schema.json").is_file()
    }


def _drift() -> set[str]:
    """Every kind the declaration and this suite disagree about; empty is the only pass.

    A declared kind that *has* a schema reaches :data:`HANDOFF_KINDS` by construction, so what
    can still drift is one declared with no schema authored for it — it would enter neither
    list, and nothing here would exercise it.
    """
    return _declared_with_no_schema_file() ^ set(KINDS_WITHOUT_A_SCHEMA)


def test_the_kinds_this_suite_exercises_are_one_enumeration_with_the_declaration() -> None:
    """`handoff.PRODUCERS` is the only place the set of kinds is written down.

    Two claims, because the tuple being derived closes only half the drift: every declared
    kind is either exercised here or named as one no schema was authored for, and that second
    list is what the schema directory actually lacks.
    """
    assert set(HANDOFF_KINDS) | KINDS_WITHOUT_A_SCHEMA == set(handoff.PRODUCERS)
    assert _drift() == set()


def test_a_kind_the_declaration_gains_is_named_here_rather_than_silently_unexercised(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The regression: a ninth kind entering one list and not the other used to be silent.

    Under the hand maintained tuple this suite simply did not parametrize the new kind, so a
    schema nobody had authored read as a live contract because it appeared in a list.
    """
    monkeypatch.setitem(handoff.PRODUCERS, "a-ninth-kind", None)
    assert _drift() == {"a-ninth-kind"}


def _schema(kind: str) -> dict:
    return json.loads((SCHEMA_DIR / f"{kind}.schema.json").read_text(encoding="utf-8"))


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_named_kind_has_a_schema_file_named_for_it(kind: str) -> None:
    """Resolution is by filename, so the name is the wiring (`catalog_source`)."""
    assert (SCHEMA_DIR / f"{kind}.schema.json").is_file()


def _demanded(schema: dict) -> set[str]:
    """Every property the schema demands somewhere: at the top level or in a branch.

    `change-summary` requires one of two field sets depending on whether the payload
    predates `basicly-gvlpxm` (`if`/`else` on `changed`), so a single `required` list can
    no longer name every property. What must still hold is that no property is optional
    *and* unmentioned, which is the fail-open shape the assertion below is about.
    """
    branches = (schema, schema.get("if", {}), schema.get("else", {}))
    return {name for branch in branches for name in branch.get("required", ())}


@pytest.mark.parametrize("kind", HANDOFF_KINDS)
def test_every_schema_is_strict_about_what_it_admits(kind: str) -> None:
    """A permissive schema is the failure this bead exists to avoid.

    `additionalProperties: false` at the top level, and every declared property demanded
    by `required` or by a conditional branch, are what make a malformed handoff a refusal
    rather than a silently thin artifact the next state reasons from.
    """
    schema = _schema(kind)
    Draft202012Validator.check_schema(schema)
    assert schema["additionalProperties"] is False
    assert _demanded(schema) == set(schema["properties"])
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

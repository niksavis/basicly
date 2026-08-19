"""The `harness-board/v1` contract: what it requires, what it tolerates, and what it refuses.

The claim under test is not "the schema parses". A contract meant to be implemented by
producers that are not this harness fails in two opposite directions, and both are here:
too strict and a foreign harness cannot adopt it, too loose and it admits anything while
looking built. So every tolerance has a paired refusal - unknown keys pass *and* a
missing required field fails, an absent section reports absent *and* a wrong major
refuses.

`test_handoff_schemas` owns the opposite discipline for the strict artifacts beside this
one; the boundary is that this schema deliberately sets no `additionalProperties: false`,
so the assertions there would be wrong here.
"""

from __future__ import annotations

import dataclasses
import json
import re
from pathlib import Path
from typing import Any

import pytest

from basicly import board_schema, cli, policy

REPO_ROOT = Path(__file__).parent.parent
FIXTURES = REPO_ROOT / "tests" / "fixtures" / "board"
SCHEMA_PATH = REPO_ROOT / ".basicly" / "core" / "schemas" / board_schema.SCHEMA_FILE

# Prose the field-selection rule keeps off the wire: the whole tracker export is 3,336,549 B
# against 33,745 B for the active rows at six selected fields (measured 2026-08-14).
FORBIDDEN_PROPERTIES = ("description", "acceptance_criteria", "body", "comments", "notes")


def _schema() -> dict[str, Any]:
    return json.loads(SCHEMA_PATH.read_text(encoding="utf-8"))


def _minimal() -> dict[str, Any]:
    return json.loads((FIXTURES / "minimal-v1.json").read_text(encoding="utf-8"))


_OFFSET = "2026-08-19T09:14:03+00:00"
_ASK = {"wait_id": "w", "kind": "checkpoint", "requested_at": _OFFSET}
_FRESH = _minimal()["freshness"]


def _subschemas(node: object) -> list[dict[str, Any]]:
    """Every subschema in the tree, so an assertion holds at depth and not only at the top."""
    if not isinstance(node, dict):
        return []
    found = [node]
    for key in ("properties", "$defs"):
        for child in node.get(key, {}).values():
            found.extend(_subschemas(child))
    for key in ("items", "additionalProperties"):
        found.extend(_subschemas(node.get(key)))
    return found


# --- The shape the contract fixes -------------------------------------------


def test_exactly_three_top_level_keys_are_required() -> None:
    """The adoption barrier: a foreign harness conforms with a four-line file."""
    schema = _schema()

    assert set(schema["required"]) == {"schema", "generated_at", "freshness"}
    assert set(schema["required"]) <= set(schema["properties"])


def test_every_section_beyond_the_three_is_optional() -> None:
    """A section is a panel; making one required would make the contract un-adoptable."""
    schema = _schema()
    optional = set(schema["properties"]) - set(schema["required"])

    assert optional == {
        "generator",
        "repo",
        "session",
        "lanes",
        "asks",
        "gates",
        "spend",
        "health",
        "backlog",
        "units",
        "graph",
        "events",
    }


def test_the_installed_schema_admits_the_version_this_module_speaks() -> None:
    """The consumer's major lives in code and the schema in a file; only this binds them."""
    assert re.fullmatch(_schema()["properties"]["schema"]["pattern"], board_schema.VERSION)


def test_freshness_requires_all_three_of_its_fields() -> None:
    """Age alone says how old the document is; these say how old it may get."""
    assert set(_schema()["properties"]["freshness"]["required"]) == {
        "source",
        "cadence_s",
        "stale_after_s",
    }


def test_a_key_may_be_added_and_a_permitted_value_may_not() -> None:
    """Both halves of one rule: no keyword closes an object, and no keyword closes a value set."""
    closed = [node for node in _subschemas(_schema()) if node.get("additionalProperties") is False]

    assert closed == []
    assert "permitted values may never be widened within a major" in _schema()["description"]


def test_no_property_admits_a_description_a_criterion_or_a_comment_body() -> None:
    """The 98.9x field-selection rule, enforced on the schema rather than on the producer."""
    named = {
        name
        for node in _subschemas(_schema())
        for name in node.get("properties", {})
        if name in FORBIDDEN_PROPERTIES
    }

    assert named == set()


def test_every_string_property_is_bounded() -> None:
    """An unbounded string is where a bead body re-enters the wire one release later."""
    unbounded = [
        node
        for node in _subschemas(_schema())
        if "string" in _types(node)
        and not {"maxLength", "enum", "pattern"} & set(node)
        and "properties" not in node
    ]

    assert unbounded == []


def _types(node: dict[str, Any]) -> set[str]:
    declared = node.get("type")
    if isinstance(declared, str):
        return {declared}
    return set(declared) if isinstance(declared, list) else set()


# --- The verdict ------------------------------------------------------------


def test_the_minimal_fixture_is_readable_and_exits_zero() -> None:
    """The bead's demonstration, through the function the command will call."""
    result = board_schema.validate_file(REPO_ROOT, FIXTURES / "minimal-v1.json")

    assert result.readable
    assert result.exit_code == 0
    assert result.summary.splitlines()[0] == "harness-board/v1, ok"


def test_the_full_fixture_reports_every_section_present() -> None:
    """The positive control: an inventory that reported nothing would pass the test below."""
    result = board_schema.validate_file(REPO_ROOT, FIXTURES / "full-v1.json")

    assert result.violations == ()
    assert result.absent == ()
    assert set(result.present) == set(_schema()["properties"]) - set(_schema()["required"])


def test_an_absent_section_is_reported_absent_and_is_not_an_error() -> None:
    """A panel nobody emits is a supported state, and the inventory is how it is named."""
    result = board_schema.validate_file(REPO_ROOT, FIXTURES / "minimal-v1.json")

    assert result.present == ()
    assert "session" in result.absent
    assert "absent    " in result.summary
    assert result.violations == ()


def test_a_differing_major_exits_two_and_names_both_versions() -> None:
    """Naming both is the difference between a refusal and an unexplained failure."""
    result = board_schema.validate_file(REPO_ROOT, FIXTURES / "wrong-major.json")

    assert result.outcome == board_schema.WRONG_MAJOR
    assert result.exit_code == 2
    assert "harness-board/v2" in result.summary
    assert "harness-board/v1" in result.summary


def test_a_differing_major_is_refused_before_its_body_is_judged() -> None:
    """A v2 document may validate field for field and still mean something else."""
    document = _minimal() | {"schema": "harness-board/v2", "freshness": "not an object"}

    result = board_schema.verdict(REPO_ROOT, document)

    assert result.exit_code == 2
    assert result.violations == ()


def test_unknown_keys_exit_zero_and_are_counted_at_every_depth() -> None:
    """The tolerance direction, and the count is what keeps it from being a silent drop."""
    document = _minimal() | {
        "invented": 1,
        "freshness": _minimal()["freshness"] | {"jitter_s": 2},
        "lanes": [{"id": "x", "phase": "build", "temperature": 0.4}],
    }

    result = board_schema.verdict(REPO_ROOT, document)

    assert result.exit_code == 0
    assert set(result.unknown) == {"$.invented", "$.freshness.jitter_s", "$.lanes[0].temperature"}
    assert "unknown   3 key(s)" in result.summary


def test_a_priority_map_key_is_not_an_unknown_key() -> None:
    """`by_priority` declares its values through `additionalProperties`, so its keys count."""
    document = _minimal() | {"backlog": {"by_priority": {"P0": 4, "P7": 1}}}

    result = board_schema.verdict(REPO_ROOT, document)

    assert result.unknown == ()
    assert result.exit_code == 0


# --- What it refuses --------------------------------------------------------


@pytest.mark.parametrize(
    ("withheld", "mutation", "expected"),
    [
        ((), {"generated_at": "14 Aug 2026"}, "$.generated_at"),
        ((), {"freshness": {"source": "one-shot", "cadence_s": None}}, "$.freshness"),
        ((), {"freshness": _FRESH | {"source": "push"}}, "$.freshness.source"),
        (("asks",), {"asks": [{"wait_id": "w", "kind": "checkpoint"}]}, "$.asks[0]"),
        (("spend",), {"spend": {"lifetime_usd": 1.0}}, "$.spend"),
        (("spend",), {"spend": {"scope": "team-wide"}}, "$.spend.scope"),
        (("lanes",), {"lanes": [{"id": "x", "phase": "build", "context_used": 5}]}, "$.lanes[0]"),
        (("gates",), {"gates": {"checks": [{"name": "ruff", "status": "green"}]}}, "$.gates"),
        (
            ("asks",),
            {"asks": [_ASK | {"actions": [{"offer": "wipe it", "basicly": "rm-rf"}]}]},
            "$.asks[0].actions[0].basicly",
        ),
    ],
)
def test_only_a_broken_required_key_refuses_the_document(
    withheld: tuple[str, ...], mutation: dict[str, Any], expected: str
) -> None:
    """An empty withheld column is a blank screen; a named one costs that one panel."""
    refused = not withheld

    result = board_schema.verdict(REPO_ROOT, _minimal() | {"backlog": {"total": 1}} | mutation)

    assert result.withheld == withheld
    assert result.outcome == (board_schema.INVALID if refused else board_schema.PARTIAL)
    assert result.exit_code == (1 if refused else board_schema.PARTLY_RENDERABLE)
    assert result.renderable == (() if refused else ("backlog",))
    assert any(line.startswith(expected) for line in result.violations), result.violations


def test_a_repo_without_the_contract_installed_cannot_report_a_pass(tmp_path: Path) -> None:
    """An explicit ask that cannot be answered must not answer yes - that is a fail-open gate."""
    result = board_schema.verdict(tmp_path, _minimal())

    assert not board_schema.adopted(tmp_path)
    assert result.outcome == board_schema.NOT_INSTALLED
    assert result.exit_code == 1
    # The path it names is repo-relative: an absolute one carries a username off-machine.
    assert board_schema.SCHEMA_FILE in result.summary
    assert str(tmp_path) not in result.summary


def test_this_repo_carries_the_contract() -> None:
    """Positive control for the case above: it must be absence that produced it."""
    assert board_schema.adopted(REPO_ROOT)


def test_a_file_that_will_not_decode_is_unreadable_rather_than_invalid(tmp_path: Path) -> None:
    """A truncated write is not a contract violation, and must not be reported as one."""
    path = tmp_path / "snapshot.json"
    path.write_text("{ not json", encoding="utf-8")

    result = board_schema.validate_file(REPO_ROOT, path)

    assert result.outcome == board_schema.UNREADABLE
    assert result.exit_code == 1


def test_a_missing_file_is_unreadable_rather_than_a_traceback(tmp_path: Path) -> None:
    """The path a caller mistyped is the one thing the message has to carry."""
    result = board_schema.validate_file(REPO_ROOT, tmp_path / "absent.json")

    assert result.outcome == board_schema.UNREADABLE
    assert result.exit_code == 1


@pytest.mark.parametrize(
    ("declared", "major"),
    [("harness-board/v1", 1), ("harness-board/v12", 12), ("board/v1", None), ("", None)],
)
def test_the_declared_major_is_read_off_the_document(declared: str, major: int | None) -> None:
    """Read, not validated: a document about to be refused still has to be named."""
    assert board_schema.declared_major({"schema": declared}) == major


def test_a_broken_section_is_withheld_while_a_sound_one_renders() -> None:
    """The bead's own case, through the file the command reads: one over-long name, one panel."""
    result = board_schema.validate_file(REPO_ROOT, FIXTURES / "broken-section-v1.json")

    assert result.renderable == ("backlog",)
    assert result.withheld == ("units",)
    assert result.exit_code not in {0, 1, board_schema.REFUSED}
    units = next(section for section in result.sections if section.name == "units")
    assert any("title" in line for line in units.violations), units.violations
    assert "withheld  units" in result.summary


# One offset row per property reading `$defs/instant`: re-inlining the pattern at any of the
# five sites fails here, which counting `$ref`s would not.
@pytest.mark.parametrize(
    "mutation",
    [
        {"generated_at": "2026-08-19T09:14:03+00:00"},
        {"generated_at": "2026-08-19T11:14:03+02:00"},
        {"lanes": [{"id": "x", "phase": "build", "started_at": _OFFSET}]},
        {"asks": [{"wait_id": "w", "kind": "review", "requested_at": _OFFSET}]},
        {"gates": {"recorded_at": _OFFSET}},
        {"events": [{"at": _OFFSET, "kind": "dispatched"}]},
        {"freshness": {"source": "state-change", "cadence_s": None, "stale_after_s": 30}},
        {"asks": [_ASK | {"actions": [{"offer": "merge the pull request"}]}]},
    ],
)
def test_a_value_the_contract_used_to_refuse_now_validates(mutation: dict[str, Any]) -> None:
    """Each row is what a foreign producer's first honest attempt carried and lost the board for.

    The last two are the extensible half: a state-change producer, and an offer naming no verb
    this consumer implements - ignored, neither an unknown key nor fatal.
    """
    result = board_schema.verdict(REPO_ROOT, _minimal() | mutation)

    assert result.exit_code == 0
    assert result.violations == ()
    assert result.unknown == ()


# --- the closed enums are bound to the engine, not merely written down ----------
#
# An enum is closed exactly where a consumer *acts* on the value, which makes each one
# a promise about something the engine already has. Nothing noticed if the two drifted
# until this pair. A published contract naming an action no command implements is worse
# than an open string, because a consumer builds a button for it.


def test_every_offered_action_names_a_command_the_cli_implements() -> None:
    """A viewer's button has to reach a verb, or the board offers what nobody can do.

    The mapping is spelled out rather than derived. The enum exists to be a *closed*
    table, so a derivation that built the command path out of the enum string would
    agree with itself and prove nothing.
    """
    offered = set(
        _schema()["properties"]["asks"]["items"]["properties"]["actions"]["items"]["properties"][
            "basicly"
        ]["enum"]
    )
    implemented = {
        "loop-answer": ("loop", "answer"),
        "checkpoint-approve": ("policy", "checkpoint"),
        "lane-kill": ("loop", "kill"),
    }
    assert offered == set(implemented), "an action moved without its verb"

    # `--help` on a real command path exits 0; an unknown word exits 2. Asked of the
    # parser the entry point builds, rather than of its internals, so the assertion
    # survives an argparse refactor and reads the same surface a consumer types.
    for action, path in implemented.items():
        with pytest.raises(SystemExit) as exit_info:
            cli._build_parser().parse_known_args([*path, "--help"])
        assert exit_info.value.code == 0, f"{action} names a command basicly does not have"


def test_a_gate_check_status_covers_every_state_the_engine_records() -> None:
    """Three schema values against the three buckets `policy.GateStatus` carries.

    A fourth engine state with no schema value gets rendered as one of these three, and
    every wrong choice reads to a human as reassurance.
    """
    declared = set(
        _schema()["properties"]["gates"]["properties"]["checks"]["items"]["properties"]["status"][
            "enum"
        ]
    )
    recorded = {
        field.name.removeprefix("required_")
        for field in dataclasses.fields(policy.GateStatus)
        if field.name.startswith("required_")
    }

    assert declared == {"pass", "fail", "not_run"}
    assert recorded == {"passed", "failed", "missing"}

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from typing import Any

import pytest

from basicly import mirror, owned_store, write_verbs
from basicly.owned_store import TrackerDivergenceError

REPO_ROOT = Path(__file__).parent.parent
KIT_DIR = REPO_ROOT / ".basicly" / "core" / "kit" / "tracker"


@pytest.fixture(scope="module")
def kit() -> Any:
    return owned_store.kit(REPO_ROOT)


def _kit_commands() -> Any:
    spec = importlib.util.spec_from_file_location("tracker_commands_pin", KIT_DIR / "commands.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["tracker_commands_pin"] = module
    spec.loader.exec_module(module)
    return module


def test_the_close_reason_field_matches_the_one_the_kit_writes() -> None:

    assert write_verbs.CLOSE_REASON_FIELD == _kit_commands().CLOSE_REASON_FIELD


def test_a_close_reason_lands_as_the_field_the_fold_reads(kit: Any) -> None:
    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1", "--reason", "shipped it"], "")
    field, status = drafts

    assert field.kind == kit.events.KIND_FIELD
    assert field.payload["name"] == write_verbs.CLOSE_REASON_FIELD
    assert field.payload["value"] == "shipped it"
    assert status.kind == kit.events.KIND_STATUS


def test_a_close_carrying_no_reason_appends_exactly_what_it_did_before(kit: Any) -> None:

    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1"], "")

    assert len(drafts) == 1
    assert drafts[0].kind == kit.events.KIND_STATUS


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_blank_reason_is_not_recorded_as_a_field(kit: Any, reason: str) -> None:
    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1", "--reason", reason], "")

    assert [draft.kind for draft in drafts] == [kit.events.KIND_STATUS]


def test_create_without_a_title_is_refused_before_anything_is_appended(kit: Any) -> None:

    with pytest.raises(TrackerDivergenceError, match="names no title"):
        mirror.drafts(kit, ["create", "--json"], json.dumps({"id": "b-9"}))


@pytest.mark.parametrize("argv", [["create"], ["create", "  "], ["create", "-t", "bug"]])
def test_a_create_naming_no_title_is_refused_whatever_else_the_argv_carries(
    kit: Any, argv: list[str]
) -> None:
    with pytest.raises(TrackerDivergenceError, match="names no title"):
        mirror.drafts(kit, argv, json.dumps({"id": "b-9"}))


def test_a_create_naming_a_title_appends_exactly_what_it_did_before(kit: Any) -> None:
    drafts: list[Any] = mirror.drafts(
        kit, ["create", "a real title", "-t", "bug"], json.dumps({"id": "b-9"})
    )

    created = [d for d in drafts if d.kind == kit.events.KIND_CREATED]
    assert len(created) == 1
    assert created[0].payload["title"] == "a real title"


def test_the_refusal_uses_the_error_class_the_sibling_already_raises() -> None:
    assert write_verbs.TrackerDivergenceError is TrackerDivergenceError


def test_a_create_carrying_a_stray_positional_is_refused_naming_it_and_its_flag(kit: Any) -> None:

    with pytest.raises(TrackerDivergenceError) as refusal:
        mirror.drafts(kit, ["create", "a real title", "bug", "1"], json.dumps({"id": "b-9"}))

    message = str(refusal.value)
    assert "create" in message
    assert "'bug'" in message
    assert "'1'" in message
    assert "--type" in message
    assert "--priority" in message


def test_a_create_whose_values_sit_on_their_flags_records_both_fields(kit: Any) -> None:
    drafts: list[Any] = mirror.drafts(
        kit, ["create", "a real title", "-t", "bug", "-p", "1"], json.dumps({"id": "b-9"})
    )

    created = next(draft for draft in drafts if draft.kind == kit.events.KIND_CREATED)
    assert created.payload["issue_type"] == "bug"
    assert created.payload["priority"] == 1


@pytest.mark.parametrize(
    "argv",
    [
        ["close", "b-1", "b-2", "b-3"],
        ["dep", "add", "b-1", "b-2", "-t", "blocks"],
        ["update", "b-1", "b-2", "-p", "1"],
    ],
)
def test_a_verb_that_legitimately_takes_several_positionals_is_not_refused(
    kit: Any, argv: list[str]
) -> None:

    assert mirror.drafts(kit, argv, "")

"""Tests for what one write verb states about a record (basicly-5m2xfd, basicly-1qi0sz).

The module `mirror.py` could not hold: it sat at 24 tokens of headroom, and neither refusal
below fitted. Its sibling owns dispatch and the untranslatable-verb refusal; this owns what a
verb means.

**Two agreements are pinned here because nothing else can see both sides.** The field a close
reason lands under is declared in the engine and in the kit, and the kit module the mirror is
handed is `differential`, which exposes `events` and `migrate` and not `commands` — so the
engine cannot read the kit's constant at runtime. Asserted from the test side, which loads the
kit by path, exactly as `labels.WRITER_LABELS` is pinned against the engine's own provenance
constants.
"""

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
    """This repo's installed tracker kit, loaded once."""
    return owned_store.kit(REPO_ROOT)


def _kit_commands() -> Any:
    """The kit's `commands` module, loaded by path — the mirror's kit module lacks it."""
    spec = importlib.util.spec_from_file_location("tracker_commands_pin", KIT_DIR / "commands.py")
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules["tracker_commands_pin"] = module
    spec.loader.exec_module(module)
    return module


def test_the_close_reason_field_matches_the_one_the_kit_writes() -> None:
    """The agreement the engine cannot reach at runtime, so it is asserted here.

    A rename on either side silently sends every close reason to a field nothing folds, which
    is the shape `basicly-5m2xfd` records: the reason went nowhere while the seam printed
    `recorded:`.
    """
    assert write_verbs.CLOSE_REASON_FIELD == _kit_commands().CLOSE_REASON_FIELD


def test_a_close_reason_lands_as_the_field_the_fold_reads(kit: Any) -> None:
    """The acceptance criterion: read back, the reason is on the record as a field."""
    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1", "--reason", "shipped it"], "")
    field, status = drafts

    assert field.kind == kit.events.KIND_FIELD
    assert field.payload["name"] == write_verbs.CLOSE_REASON_FIELD
    assert field.payload["value"] == "shipped it"
    assert status.kind == kit.events.KIND_STATUS


def test_a_close_carrying_no_reason_appends_exactly_what_it_did_before(kit: Any) -> None:
    """The second criterion: no existing record's history changes meaning.

    A close without a reason must still be one status event and nothing else, or every close
    already in the log would read as having lost a field it never had.
    """
    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1"], "")

    assert len(drafts) == 1
    assert drafts[0].kind == kit.events.KIND_STATUS


@pytest.mark.parametrize("reason", ["", "   "])
def test_a_blank_reason_is_not_recorded_as_a_field(kit: Any, reason: str) -> None:
    """An empty reason is the absence of one, not a field whose value is a space."""
    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1", "--reason", reason], "")

    assert [draft.kind for draft in drafts] == [kit.events.KIND_STATUS]


def test_create_without_a_title_is_refused_before_anything_is_appended(kit: Any) -> None:
    """`create --help` minted a record carrying only its provenance (basicly-1qi0sz).

    `_close_drafts` twelve lines away already refused an argv naming no record, so the
    refusal pattern was in the same function group and was not being reused.
    """
    with pytest.raises(TrackerDivergenceError, match="names no title"):
        mirror.drafts(kit, ["create", "--json"], json.dumps({"id": "b-9"}))


@pytest.mark.parametrize("argv", [["create"], ["create", "  "], ["create", "-t", "bug"]])
def test_a_create_naming_no_title_is_refused_whatever_else_the_argv_carries(
    kit: Any, argv: list[str]
) -> None:
    """A flag is not a title, and neither is whitespace."""
    with pytest.raises(TrackerDivergenceError, match="names no title"):
        mirror.drafts(kit, argv, json.dumps({"id": "b-9"}))


def test_a_create_naming_a_title_appends_exactly_what_it_did_before(kit: Any) -> None:
    """The second criterion: a create that names a title is unchanged."""
    drafts: list[Any] = mirror.drafts(
        kit, ["create", "a real title", "-t", "bug"], json.dumps({"id": "b-9"})
    )

    created = [d for d in drafts if d.kind == kit.events.KIND_CREATED]
    assert len(created) == 1
    assert created[0].payload["title"] == "a real title"


def test_the_refusal_uses_the_error_class_the_sibling_already_raises() -> None:
    """The third criterion: one class, not a second one beside it."""
    assert write_verbs.TrackerDivergenceError is TrackerDivergenceError

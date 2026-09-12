from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basicly import mirror, owned_store, owned_write, tracker_argv
from basicly.owned_store import TrackerDivergenceError
from tests.test_owned_write import PARENT, events_of, no_br, owned_repo, seed

__all__ = ["no_br"]

REPO_ROOT = Path(__file__).resolve().parent.parent

ABSENT = "wpc-nope"


@pytest.fixture(scope="module")
def kit() -> Any:
    return owned_store.kit(REPO_ROOT)


def _one(drafts: list[Any]) -> Any:
    assert len(drafts) == 1, drafts
    return drafts[0]


def _by_kind(drafts: list[Any], kind: str) -> Any:
    matching = [draft for draft in drafts if draft.kind == kind]
    assert len(matching) == 1, (kind, drafts)
    return matching[0]


@pytest.mark.parametrize(
    "args",
    [["show", "b-1", "--json"], ["list", "--status", "open"], ["ready"]],
)
def test_a_read_states_nothing_about_a_record(kit: Any, args: list[str]) -> None:
    assert mirror.drafts(kit, args, "") == []


@pytest.mark.parametrize("write", ["init", "sync"])
def test_a_write_that_states_nothing_about_a_record_is_skipped(kit: Any, write: str) -> None:

    assert mirror.drafts(kit, [write], "") == []


def test_an_untranslated_write_is_refused_and_names_both_repairs(kit: Any) -> None:

    with pytest.raises(TrackerDivergenceError) as excinfo:
        mirror.drafts(kit, ["label", "add", "b-1", "phase-6"], "")

    message = str(excinfo.value)
    assert "_MIRRORED_WRITES" in message
    assert "UNMIRRORED_WRITES" in message


def test_a_comment_body_beginning_with_a_dash_survives(kit: Any) -> None:

    body = "-- checkpoint: ship approved"

    draft = _one(mirror.drafts(kit, ["comments", "add", "b-1", body], ""))

    assert draft.record == "b-1"
    assert draft.kind == kit.events.KIND_COMMENT
    assert draft.payload["text"] == body


def test_a_comment_with_the_wrong_argument_count_is_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="one issue and one body"):
        mirror.drafts(kit, ["comments", "add", "b-1", "body", "extra"], "")


def test_a_created_records_labels_survive_a_read_as_two_labels(kit: Any) -> None:

    args = ["create", "a title", "-l", "phase-6,ready", "-p", "1", "-t", "task"]

    drafts = mirror.drafts(kit, args, json.dumps({"id": "b-9", "status": "open"}))

    created = _by_kind(drafts, kit.events.KIND_CREATED)
    assert created.record == "b-9"
    assert tracker_argv.labels_of(created.payload["labels"]) == ("phase-6", "ready")
    assert created.payload["priority"] == 1
    assert created.payload["title"] == "a title"


def test_a_parent_becomes_an_edge_under_the_kits_own_name(kit: Any) -> None:

    args = ["create", "a title", "--parent", "b-0"]

    drafts = mirror.drafts(kit, args, json.dumps({"id": "b-9"}))

    edge = _by_kind(drafts, kit.migrate.KIND_EDGE)
    assert edge.record == "b-9"
    assert edge.payload[kit.migrate.EDGE_FROM] == "b-9"
    assert edge.payload[kit.migrate.EDGE_TO] == "b-0"
    assert edge.payload[kit.migrate.EDGE_TYPE] == kit.DEFAULT_VOCABULARY.parent_child_type


def test_a_create_whose_reply_carries_no_id_is_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="no issue id"):
        mirror.drafts(kit, ["create", "a title"], json.dumps({"status": "open"}))


def test_a_create_whose_reply_is_not_json_is_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="no JSON record"):
        mirror.drafts(kit, ["create", "a title"], "created b-9")


def test_a_reply_with_no_status_falls_back_to_the_status_br_gives_a_new_record(
    kit: Any,
) -> None:
    drafts = mirror.drafts(kit, ["create", "a title"], json.dumps({"id": "b-9"}))

    assert _by_kind(drafts, kit.events.KIND_STATUS).payload["status"] == "open"


def test_the_gated_issue_is_read_past_the_flags_that_precede_it(kit: Any) -> None:

    args = [
        "gate",
        "report",
        "--gate",
        "verify",
        "--provider",
        "basicly-verify",
        "--status",
        "pass",
        "--note",
        "all green",
        "b-1",
    ]

    draft = _one(mirror.drafts(kit, args, ""))

    assert draft.record == "b-1"
    assert draft.kind == kit.KIND_GATE
    assert draft.payload[kit.GATE_NAME_KEY] == "verify"
    assert draft.payload[kit.GATE_PROVIDER_KEY] == "basicly-verify"
    assert draft.payload[kit.GATE_PASSED_KEY] is True


@pytest.mark.parametrize("status", ["fail", "", "PASS", "passed"])
def test_only_pass_reads_as_a_passing_gate(kit: Any, status: str) -> None:
    args = ["gate", "report", "--gate", "verify", "--provider", "p", "--status", status, "b"]

    assert _one(mirror.drafts(kit, args, "")).payload[kit.GATE_PASSED_KEY] is False


def test_a_gate_report_missing_its_gate_or_provider_is_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="no gate and provider"):
        mirror.drafts(kit, ["gate", "report", "--gate", "verify", "b-1"], "")


def test_an_edge_is_recorded_on_the_dependent(kit: Any) -> None:
    args = ["dep", "add", "b-2", "b-1", "-t", "blocks"]

    draft = _one(mirror.drafts(kit, args, ""))

    assert draft.record == "b-2"
    assert draft.payload[kit.migrate.EDGE_FROM] == "b-2"
    assert draft.payload[kit.migrate.EDGE_TO] == "b-1"
    assert draft.payload[kit.migrate.EDGE_TYPE] == "blocks"


def test_an_edge_with_no_type_is_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="no edge type"):
        mirror.drafts(kit, ["dep", "add", "b-2", "b-1"], "")


def test_a_dep_remove_retracts_exactly_the_edge_a_dep_add_would_have_written(kit: Any) -> None:

    added = _one(mirror.drafts(kit, ["dep", "add", "b-2", "b-1", "-t", "blocks"], ""))

    removed = _one(mirror.drafts(kit, ["dep", "remove", "b-2", "b-1", "-t", "blocks"], ""))

    assert added.kind == kit.migrate.KIND_EDGE
    assert removed.kind == kit.events.KIND_EDGE_RETRACTED
    assert removed.record == added.record == "b-2"
    assert removed.payload == added.payload


def test_a_dep_remove_with_no_type_is_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="no edge type"):
        mirror.drafts(kit, ["dep", "remove", "b-2", "b-1"], "")


def test_a_dep_remove_of_a_parent_child_edge_is_refused_and_says_what_it_would_change(
    kit: Any,
) -> None:

    parent_child = kit.DEFAULT_VOCABULARY.parent_child_type

    with pytest.raises(TrackerDivergenceError, match="not retractable") as excinfo:
        mirror.drafts(kit, ["dep", "remove", "b-2", "b-1", "-t", parent_child], "")

    assert "supervise" in str(excinfo.value)
    assert mirror.drafts(kit, ["dep", "remove", "b-2", "b-1", "-t", "blocks"], "")


def test_every_mirrored_event_says_how_the_fact_got_here(kit: Any) -> None:

    every: list[Any] = [
        *mirror.drafts(kit, ["close", "b-1", "--reason", "done"], ""),
        *mirror.drafts(kit, ["comments", "add", "b-1", "text"], ""),
        *mirror.drafts(kit, ["update", "-s", "open", "b-1"], ""),
        *mirror.drafts(kit, ["dep", "add", "b-2", "b-1", "-t", "blocks"], ""),
        *mirror.drafts(kit, ["create", "t", "--parent", "b-0"], json.dumps({"id": "b-9"})),
    ]

    assert every
    assert all(
        draft.payload[kit.migrate.PROVENANCE_KEY] == mirror.MIRROR_PROVENANCE for draft in every
    )
    assert kit.migrate.PROVENANCE_KEY in kit.migrate.RESERVED_KEYS


def test_a_multi_id_close_moves_every_id_it_names(kit: Any) -> None:

    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1", "b-2", "b-3", "--reason", "done"], "")
    moved = [d for d in drafts if d.kind == kit.events.KIND_STATUS]

    assert [draft.record for draft in moved] == ["b-1", "b-2", "b-3"]
    assert {draft.payload["status"] for draft in moved} == {"closed"}


def test_a_close_naming_no_id_is_still_refused(kit: Any) -> None:
    with pytest.raises(TrackerDivergenceError, match="names no issue"):
        mirror.drafts(kit, ["close", "--reason", "done"], "")


@pytest.mark.usefixtures("no_br")
def test_an_update_absent_from_the_ledger_is_refused_and_names_the_id(
    kit: Any, tmp_path: Path
) -> None:

    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    with pytest.raises(TrackerDivergenceError) as refusal:
        owned_write.append(repo, ["update", ABSENT, "-t", "bug"])

    assert ABSENT in str(refusal.value)
    assert events_of(repo, ABSENT) == []

    owned_write.append(repo, ["update", PARENT, "-t", "bug"])

    fields = [e for e in events_of(repo, PARENT) if e.kind == kit.events.KIND_FIELD]
    assert [e.payload["value"] for e in fields] == ["bug"]


@pytest.mark.parametrize(
    "args",
    [
        ["close", ABSENT],
        ["comments", "add", ABSENT, "a marker"],
        ["dep", "add", ABSENT, PARENT, "-t", "blocks"],
        ["gate", "report", ABSENT, "--gate", "build", "--provider", "cli", "--status", "pass"],
    ],
    ids=["close", "comments add", "dep add", "gate report"],
)
@pytest.mark.usefixtures("no_br")
def test_every_write_verb_refuses_a_record_the_ledger_does_not_hold(
    tmp_path: Path, args: list[str]
) -> None:
    repo = owned_repo(tmp_path)
    seed(repo, PARENT)

    with pytest.raises(TrackerDivergenceError, match=ABSENT):
        owned_write.append(repo, args)

    assert events_of(repo, ABSENT) == []

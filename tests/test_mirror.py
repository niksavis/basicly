"""One accepted `br` write, translated into the events the owned ledger records it with.

Written when the §9.4 naming gate was made binding (basicly-u2hl.14). `test_br_seam.py`
already drove the translation end-to-end — a stand-in br, a real ledger, and the record
read back — and those tests stay there, because what they assert is that the *seam*
writes both stores. What was missing is the translation on its own: which drafts one
argv becomes, and which argv is refused.

`br update`'s own flag surface outgrew this file and lives in `test_mirror_update.py`.

The kit arrives as a parameter rather than being loaded by `mirror`, and this file is
what that affordance was for: the real kit module, no repo, no ledger, no subprocess. It
is the real one rather than a stub on purpose — every key the mirror writes is read off
that module, so a stub would agree with the mirror about a name the kit does not use.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from basicly import mirror, owned_store
from basicly.owned_store import TrackerDivergenceError

REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(scope="module")
def kit() -> Any:
    """This repo's installed tracker kit, loaded once."""
    return owned_store.kit(REPO_ROOT)


def _one(drafts: list[Any]) -> Any:
    assert len(drafts) == 1, drafts
    return drafts[0]


def _by_kind(drafts: list[Any], kind: str) -> Any:
    matching = [draft for draft in drafts if draft.kind == kind]
    assert len(matching) == 1, (kind, drafts)
    return matching[0]


# --- what is not translated ----------------------------------------------------


@pytest.mark.parametrize(
    "args",
    [["show", "b-1", "--json"], ["list", "--status", "open"], ["ready"]],
)
def test_a_read_states_nothing_about_a_record(kit: Any, args: list[str]) -> None:
    """The mirror is on the write path only; a read has nothing to keep in step."""
    assert mirror.drafts(kit, args, "") == []


@pytest.mark.parametrize("write", ["init", "sync"])
def test_a_write_that_states_nothing_about_a_record_is_skipped(kit: Any, write: str) -> None:
    """`sync` moves the store between database and export; `init` creates it.

    Named rather than defaulted to "skip" — the default for an unrecognised write is a
    refusal, and this is the list that says which two are deliberate.
    """
    assert mirror.drafts(kit, [write], "") == []


def test_an_untranslated_write_is_refused_and_names_both_repairs(kit: Any) -> None:
    """The surface was frozen by measurement, so a write outside it is a new dependency.

    The mirror is the only place that can still see it before the two stores drift, so
    the message has to name what to do rather than only what went wrong.
    """
    with pytest.raises(TrackerDivergenceError) as excinfo:
        mirror.drafts(kit, ["label", "add", "b-1", "phase-6"], "")

    message = str(excinfo.value)
    assert "_MIRRORED_WRITES" in message
    assert "_UNMIRRORED_WRITES" in message


# --- comments: 45% of this repo's tracker traffic ------------------------------


def test_a_comment_body_beginning_with_a_dash_survives(kit: Any) -> None:
    """Read by position, because the body is arbitrary free text.

    Through `_positionals` a leading `-` reads as a flag and the body is dropped —
    losing exactly the checkpoint or rework marker the policy layer is carried in.
    """
    body = "-- checkpoint: ship approved"

    draft = _one(mirror.drafts(kit, ["comments", "add", "b-1", body], ""))

    assert draft.record == "b-1"
    assert draft.kind == kit.events.KIND_COMMENT
    assert draft.payload["text"] == body


def test_a_comment_with_the_wrong_argument_count_is_refused(kit: Any) -> None:
    """Positional reading is only safe while the shape is exactly the one assumed."""
    with pytest.raises(TrackerDivergenceError, match="one issue and one body"):
        mirror.drafts(kit, ["comments", "add", "b-1", "body", "extra"], "")


# --- create --------------------------------------------------------------------


def test_a_created_records_labels_are_stored_as_a_list(kit: Any) -> None:
    """A stored `"phase-6,ready"` iterates as characters, not as two labels.

    `supervise` reads `record["labels"]` as a list, so after the flip a lane's follow-up
    would inherit twelve one-letter labels. The priority alongside it is the other typed
    field, and title comes off the positional.
    """
    args = ["create", "a title", "-l", "phase-6,ready", "-p", "1", "-t", "task"]

    drafts = mirror.drafts(kit, args, json.dumps({"id": "b-9", "status": "open"}))

    created = _by_kind(drafts, kit.events.KIND_CREATED)
    assert created.record == "b-9"
    assert created.payload["labels"] == ["phase-6", "ready"]
    assert created.payload["priority"] == 1
    assert created.payload["title"] == "a title"


def test_a_parent_becomes_an_edge_under_the_kits_own_name(kit: Any) -> None:
    """A field would leave the parent undecomposed to `differential.children_of`.

    The edge type is read off the kit rather than spelled a fourth time, which is what
    this asserts — a literal here would make a mirrored parent invisible to the ready
    query.
    """
    args = ["create", "a title", "--parent", "b-0"]

    drafts = mirror.drafts(kit, args, json.dumps({"id": "b-9"}))

    edge = _by_kind(drafts, kit.migrate.KIND_EDGE)
    assert edge.record == "b-9"
    assert edge.payload[kit.migrate.EDGE_FROM] == "b-9"
    assert edge.payload[kit.migrate.EDGE_TO] == "b-0"
    assert edge.payload[kit.migrate.EDGE_TYPE] == kit.DEFAULT_VOCABULARY.parent_child_type


def test_a_create_whose_reply_carries_no_id_is_refused(kit: Any) -> None:
    """Only the reply carries the id br minted, so there is nothing to mirror without it."""
    with pytest.raises(TrackerDivergenceError, match="no issue id"):
        mirror.drafts(kit, ["create", "a title"], json.dumps({"status": "open"}))


def test_a_create_whose_reply_is_not_json_is_refused(kit: Any) -> None:
    """The other half of the reply contract: unparseable is as fatal as id-less."""
    with pytest.raises(TrackerDivergenceError, match="no JSON record"):
        mirror.drafts(kit, ["create", "a title"], "created b-9")


def test_a_reply_with_no_status_falls_back_to_the_status_br_gives_a_new_record(
    kit: Any,
) -> None:
    """Absent, not invented: a created record is open, which is what br would echo."""
    drafts = mirror.drafts(kit, ["create", "a title"], json.dumps({"id": "b-9"}))

    assert _by_kind(drafts, kit.events.KIND_STATUS).payload["status"] == "open"


# --- gate report: the writer KIND_GATE was defined for -------------------------


def test_the_gated_issue_is_read_past_the_flags_that_precede_it(kit: Any) -> None:
    """`br gate report` puts the issue id last, after four or five flag/value pairs.

    "The last argument" is only right by accident and "every non-flag token" would
    collect `--note`'s free text, so the positional walk has to consume each flag's
    value.
    """
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
    """`policy.GateStatus`'s own reading of the field, so the two cannot disagree."""
    args = ["gate", "report", "--gate", "verify", "--provider", "p", "--status", status, "b"]

    assert _one(mirror.drafts(kit, args, "")).payload[kit.GATE_PASSED_KEY] is False


def test_a_gate_report_missing_its_gate_or_provider_is_refused(kit: Any) -> None:
    """Both name the row the differential compares; one of them alone identifies nothing."""
    with pytest.raises(TrackerDivergenceError, match="no gate and provider"):
        mirror.drafts(kit, ["gate", "report", "--gate", "verify", "b-1"], "")


# --- dep add -------------------------------------------------------------------


def test_an_edge_is_recorded_on_the_dependent(kit: Any) -> None:
    """Where both stores hold it, so the differential compares like with like."""
    args = ["dep", "add", "b-2", "b-1", "-t", "blocks"]

    draft = _one(mirror.drafts(kit, args, ""))

    assert draft.record == "b-2"
    assert draft.payload[kit.migrate.EDGE_FROM] == "b-2"
    assert draft.payload[kit.migrate.EDGE_TO] == "b-1"
    assert draft.payload[kit.migrate.EDGE_TYPE] == "blocks"


def test_an_edge_with_no_type_is_refused(kit: Any) -> None:
    """An untyped edge would be indistinguishable from a parent-child one."""
    with pytest.raises(TrackerDivergenceError, match="no edge type"):
        mirror.drafts(kit, ["dep", "add", "b-2", "b-1"], "")


# --- provenance ----------------------------------------------------------------


def test_every_mirrored_event_says_how_the_fact_got_here(kit: Any) -> None:
    """§9.6: a dual-written event and one `migrate.py` extracted are different facts.

    The key is one of `migrate.RESERVED_KEYS`, so it is dropped again when a record is
    rendered back — which is why it can be carried on every kind without polluting one.
    """
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


def test_a_close_reason_is_not_mirrored_as_a_comment(kit: Any) -> None:
    """The reason is a field of the close, so mirroring a comment would invent one.

    A difference the mirror manufactured rather than found is worse than a missing one:
    the differential would report it against a reference side that never had it.
    """
    draft = _one(mirror.drafts(kit, ["close", "b-1", "--reason", "done"], ""))

    assert draft.kind == kit.events.KIND_STATUS
    assert draft.payload["status"] == "closed"


def test_a_multi_id_close_moves_every_id_it_names(kit: Any) -> None:
    """Every id br closes is recorded — its own `--help` takes `[IDS]...`.

    This refused rather than translated until `basicly-e2mz.24`, and it refused *after*
    br had closed all three — so the command failed with three beads closed on one store
    and none on the other. One draft per id, in the order the argv names them, because a
    fold over a re-ordered set is the same state but a diff against the export is not.
    """
    drafts: list[Any] = mirror.drafts(kit, ["close", "b-1", "b-2", "b-3", "--reason", "done"], "")

    assert [draft.record for draft in drafts] == ["b-1", "b-2", "b-3"]
    assert {draft.payload["status"] for draft in drafts} == {"closed"}


def test_a_close_naming_no_id_is_still_refused(kit: Any) -> None:
    """Widening to many ids must not widen to none — the control on the close above."""
    with pytest.raises(TrackerDivergenceError, match="names no issue"):
        mirror.drafts(kit, ["close", "--reason", "done"], "")

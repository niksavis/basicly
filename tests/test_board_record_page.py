from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from markupsafe import escape

from basicly import (
    board_asks,
    board_cli,
    board_facts,
    board_record,
    board_render,
    board_schema,
    board_serve,
    policy,
)
from tests.test_board_asks import TOKEN
from tests.test_board_render import TEMPLATES
from tests.test_board_wall import REPO_ROOT, STAMPED, document

QUIET = "basicly-rn0o.2"
BLOCKER = "basicly-rn0o.1"

RUNNING = "basicly-rbnz49"


def _verdict(doc: dict[str, Any]) -> board_schema.SnapshotVerdict:
    return board_schema.verdict(REPO_ROOT, doc)


def page(doc: dict[str, Any], record_id: str, *, back: str = "..") -> str | None:
    filled = board_record.context(
        doc, _verdict(doc), record_id, STAMPED, page=board_record.PageFacts(back=back)
    )
    return None if filled is None else board_render.render_record(filled, TEMPLATES)


def shown(doc: dict[str, Any], record_id: str) -> str:
    drawn = page(doc, record_id)
    assert drawn is not None, f"{record_id} draws no page"
    return drawn


def wall(doc: dict[str, Any]) -> str:

    rows, dropped = board_asks.pending(doc.get("asks"), TOKEN)
    filled = board_render.context(
        doc,
        _verdict(doc),
        STAMPED,
        acts=(
            rows,
            dropped,
            board_asks.killable(doc.get("lanes"), TOKEN),
            board_asks.parking(doc.get("units"), TOKEN),
            board_asks.starting(doc, TOKEN),
        ),
    )
    return board_render.render(filled, TEMPLATES)


@pytest.fixture
def doc() -> dict[str, Any]:
    return document("wall-v1.json")


@pytest.fixture
def with_lane(doc: dict[str, Any]) -> dict[str, Any]:
    doc["units"] = [
        *doc["units"],
        {"id": RUNNING, "title": "a lane in flight", "status": "in_progress", "phase": "build"},
    ]
    doc["detail"] = [
        *doc["detail"],
        {
            "id": RUNNING,
            "worktree": RUNNING,
            "branch": f"harness/{RUNNING}",
            "checkpoints_held": ["classify"],
            "checkpoints_missing": ["decompose", "ship"],
            "rework": {"verify": 1},
            "next_command": f"basicly loop advance {RUNNING}",
        },
    ]
    return doc


def test_the_page_answers_every_field_the_acceptance_names(doc: dict[str, Any]) -> None:
    drawn = page(doc, QUIET)
    unit = next(row for row in doc["units"] if row["id"] == QUIET)
    assert drawn is not None
    for expected in (
        QUIET,
        str(escape(unit["title"])),
        "open",
        "P1",
        "intake",
        "classify",
        "decompose",
        "verify",
        BLOCKER,
        doc["generated_at"],
    ):
        assert expected in drawn, f"the page does not say {expected!r}"


def test_a_held_checkpoint_reads_differently_from_a_missing_one(doc: dict[str, Any]) -> None:
    drawn = page(doc, BLOCKER)
    assert drawn is not None
    assert "held — classify" in drawn
    assert "not approved — ship" in drawn
    assert "not approved — classify" not in drawn


def test_the_edges_are_split_by_direction_and_keep_only_open_records(doc: dict[str, Any]) -> None:
    blockers, dependents = board_record._edges(doc, QUIET)
    assert blockers == (BLOCKER,)
    assert dependents == ("basicly-rn0o.3",)
    doc["units"] = [row for row in doc["units"] if row["id"] != BLOCKER]
    assert board_record._edges(doc, QUIET)[0] == ()


def test_the_tree_is_drawn_because_a_blocks_only_page_stranded_every_epic(
    doc: dict[str, Any],
) -> None:

    doc["graph"]["edges"] += [
        {"from": QUIET, "kind": board_record.PARENT_CHILD, "to": BLOCKER},
        {"from": "basicly-rn0o.3", "kind": board_record.PARENT_CHILD, "to": QUIET},
    ]
    assert board_record._family(doc, QUIET) == (BLOCKER, ("basicly-rn0o.3",))

    drawn = shown(doc, QUIET)
    assert "the tree" in drawn
    assert f'href="{BLOCKER}' in drawn, "a child cannot reach the parent that explains it"
    assert 'href="basicly-rn0o.3' in drawn, "a parent cannot reach the child that implements it"

    doc["units"] = [row for row in doc["units"] if row["id"] != "basicly-rn0o.3"]
    assert board_record._family(doc, QUIET)[1] == ()


def test_a_record_with_no_lane_says_so_and_prints_the_producers_own_command(
    doc: dict[str, Any],
) -> None:
    drawn = page(doc, QUIET)
    assert drawn is not None
    assert board_record.NO_LANE in drawn
    assert f"basicly loop advance {QUIET}" in drawn

    row = next(held for held in doc["detail"] if held["id"] == QUIET)
    row["next_command"] = "basicly loop advance --dry-run " + QUIET
    assert "basicly loop advance --dry-run " + QUIET in shown(doc, QUIET)

    del row["next_command"]
    withheld = shown(doc, QUIET)
    assert "basicly loop advance" not in withheld
    assert "no next command" in withheld


def test_the_running_lane_names_its_agent_model_start_spend_and_last_word(
    with_lane: dict[str, Any],
) -> None:
    lane = next(row for row in with_lane["lanes"] if row["id"] == RUNNING)
    lane["note"] = "running the verify suite"
    drawn = page(with_lane, RUNNING)
    assert drawn is not None
    for expected in (lane["agent"], lane["model"], lane["started_at"], lane["note"]):
        assert expected in drawn, f"the lane does not say {expected!r}"
    assert "18794333" in drawn.replace(",", ""), "the lane reports no spend"
    assert board_record.NO_LANE not in drawn


def test_a_field_the_snapshot_lacks_renders_as_absent_and_never_as_a_default(
    doc: dict[str, Any],
) -> None:
    del doc["detail"]
    drawn = page(doc, QUIET)
    assert drawn is not None
    assert drawn.count("not emitted by this producer") >= 1
    assert "none bound" not in drawn, "an unreported binding is drawn as no binding"
    assert "not approved" not in drawn, "an unreported checkpoint is drawn as outstanding"
    assert "basicly loop advance" not in drawn


def test_no_page_is_drawn_for_a_record_the_document_never_listed(doc: dict[str, Any]) -> None:
    assert page(doc, "basicly-nothing") is None
    assert page(doc, "../../etc/passwd") is None


@pytest.mark.parametrize(
    "ident", ["../escape", "/absolute", "sub/dir", ".hidden", "with space", ""]
)
def test_an_id_that_cannot_be_a_file_name_is_refused(ident: str) -> None:
    assert not board_record.writable(ident)


def test_every_region_that_prints_an_id_links_it_to_that_records_page(
    with_lane: dict[str, Any],
) -> None:

    asked = with_lane["asks"][0]["issue"]
    with_lane["units"] = [
        *with_lane["units"],
        {"id": "basicly-parked1", "status": "deferred", "title": "parked work", "priority": "P3"},
        {"id": asked, "status": "open", "title": "the record an ask names", "priority": "P1"},
    ]
    drawn = wall(with_lane)
    for ident in (QUIET, RUNNING, "basicly-parked1"):
        assert f'href="record/{ident}.html"' in drawn, f"{ident} is printed but not linked"
    for row in (with_lane["events"][-1], with_lane["asks"][0]):
        assert f'href="record/{row["issue"]}.html"' in drawn


def test_an_id_with_no_page_behind_it_is_printed_and_not_linked(doc: dict[str, Any]) -> None:

    doc["events"][-1] = {**doc["events"][-1], "issue": "basicly-closed9"}
    drawn = wall(doc)
    assert "basicly-closed9" in drawn, "the event strip dropped the record it names"
    assert 'href="record/basicly-closed9.html"' not in drawn


def test_a_clipped_row_carries_the_untruncated_title(doc: dict[str, Any]) -> None:
    long_title = "a title far longer than the ready column can hold " * 3
    row = next(unit for unit in doc["units"] if unit["id"] == QUIET)
    row["title"] = long_title
    drawn = wall(doc)
    assert f'title="{long_title}"' in drawn
    assert f"{long_title}</td>" not in drawn, "the column is not clipping at all"


def test_the_rework_count_is_the_engines_own_count(work_repo: Path) -> None:

    gathered = board_facts.details(work_repo)
    assert gathered is not None
    counted = [held for held in gathered if any(held.rework.values())]
    assert counted, "no record in this corpus records a rework attempt; the probe is blunt"
    for held in counted:
        for gate, attempts in held.rework.items():
            assert attempts == policy.rework_attempts(work_repo, held.id, gate), (
                f"{held.id} gate {gate}"
            )


def test_mode_a_writes_a_page_the_walls_own_link_resolves_to(tmp_path: Path) -> None:
    doc = document("wall-v1.json")
    out = tmp_path / "board.html"
    out.write_text(wall(doc), encoding="utf-8")
    written, refused = board_cli._write_records(doc, _verdict(doc), out, STAMPED, {})
    assert written == len(doc["units"])
    assert refused == 0
    landed = out.parent / board_record.href(QUIET)
    assert landed.is_file(), "the wall links to a page nobody wrote"
    assert QUIET in landed.read_text(encoding="utf-8")
    assert 'href="../board.html"' in landed.read_text(encoding="utf-8")


def test_the_server_answers_from_the_document_it_is_serving() -> None:

    board = board_serve.Board(REPO_ROOT, build=dict)
    board._served = json.dumps(document("wall-v1.json")).encode("utf-8")

    drawn = board.record(QUIET, STAMPED)
    assert drawn is not None
    assert QUIET.encode() in drawn
    assert board.record("basicly-nothing", STAMPED) is None
    assert board.record("../../etc/passwd", STAMPED) is None
    assert f"/{board_record.HREF_DIR}/" == board_serve.RECORD_ROUTE


def test_the_page_dates_itself_against_the_snapshot_it_was_drawn_from(doc: dict[str, Any]) -> None:

    assert doc["generated_at"] in shown(doc, QUIET)
    later = datetime(2026, 8, 22, 16, 42, 52, tzinfo=UTC)
    filled = board_record.context(doc, _verdict(doc), QUIET, later)
    assert filled is not None
    assert "24h 0m ago" in board_render.render_record(filled, TEMPLATES)

"""One record's page: what it must say, and what it must refuse to say (basicly-62h3x9).

Against the rendered bytes wherever the claim is about the page, following
`tests/test_board_operator_questions.py`: a context dict holding the right keys has passed
here before while the template drew none of them.

The two mutation directions are both run on the one field the acceptance is strictest about.
Removing `next_command` must leave the page saying absent - that proves the page reads the
producer's remedy - and rewording it must move the page, which proves the page is not
composing the same string itself and reading as if it had.
"""

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

# A unit of the wall fixture with no lane, and one of its blockers.
QUIET = "basicly-rn0o.2"
BLOCKER = "basicly-rn0o.1"

# A lane the fixture carries. It has no unit row, so a test that wants the lane half of the
# page adds one: `units` and `lanes` in this fixture are different populations.
RUNNING = "basicly-rbnz49"


def _verdict(doc: dict[str, Any]) -> board_schema.SnapshotVerdict:
    return board_schema.verdict(REPO_ROOT, doc)


def page(doc: dict[str, Any], record_id: str, *, back: str = "..") -> str | None:
    """One record's page as a reader receives it, or None where the document lists no such id."""
    filled = board_record.context(
        doc, _verdict(doc), record_id, STAMPED, page=board_record.PageFacts(back=back)
    )
    return None if filled is None else board_render.render_record(filled, TEMPLATES)


def shown(doc: dict[str, Any], record_id: str) -> str:
    """:func:`page`, refusing None: a test reading fields wants the page, not the absence."""
    drawn = page(doc, record_id)
    assert drawn is not None, f"{record_id} draws no page"
    return drawn


def wall(doc: dict[str, Any]) -> str:
    """The whole board page, with the action surface assembled: that is what draws the asks.

    Without the surface the ask band is empty, and a test for the link on an ask row would
    pass or fail on whether the rows were built at all.
    """
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
    """The wall fixture, parsed fresh so a mutating test cannot reach another one."""
    return document("wall-v1.json")


@pytest.fixture
def with_lane(doc: dict[str, Any]) -> dict[str, Any]:
    """The fixture, with a unit and a detail row for the lane it already carries."""
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
    """The record's own state, its binding, its checkpoints, its rework and its edges."""
    drawn = page(doc, QUIET)
    unit = next(row for row in doc["units"] if row["id"] == QUIET)
    assert drawn is not None
    for expected in (
        QUIET,
        str(escape(unit["title"])),  # the page is autoescaped; the fixture title has an apostrophe
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
    """Both lists are drawn, so a reader learns what is outstanding and not only what is done."""
    drawn = page(doc, BLOCKER)
    assert drawn is not None
    assert "held — classify" in drawn
    assert "not approved — ship" in drawn
    # The control: the same page must not call a held checkpoint outstanding.
    assert "not approved — classify" not in drawn


def test_the_edges_are_split_by_direction_and_keep_only_open_records(doc: dict[str, Any]) -> None:
    """What this record waits on, against what waits on it."""
    blockers, dependents = board_record._edges(doc, QUIET)
    assert blockers == (BLOCKER,)
    assert dependents == ("basicly-rn0o.3",)
    # A closed blocker holds nothing: an edge onto a record `units` no longer lists is dropped.
    doc["units"] = [row for row in doc["units"] if row["id"] != BLOCKER]
    assert board_record._edges(doc, QUIET)[0] == ()


def test_the_tree_is_drawn_because_a_blocks_only_page_stranded_every_epic(
    doc: dict[str, Any],
) -> None:
    """The parent a record belongs to, and the children that belong to it.

    The fixture carries no `parent-child` edge at all, which is why a page reading only
    `blocks` looked complete here: the real snapshot is mostly parent-child, and against it an
    epic's page said nothing open waited on it while fourteen children were open. The edges
    are added rather than assumed so the assertion has a population to fail against.
    """
    doc["graph"]["edges"] += [
        {"from": QUIET, "kind": board_record.PARENT_CHILD, "to": BLOCKER},
        {"from": "basicly-rn0o.3", "kind": board_record.PARENT_CHILD, "to": QUIET},
    ]
    assert board_record._family(doc, QUIET) == (BLOCKER, ("basicly-rn0o.3",))

    drawn = shown(doc, QUIET)
    assert "the tree" in drawn
    assert f'href="{BLOCKER}' in drawn, "a child cannot reach the parent that explains it"
    assert 'href="basicly-rn0o.3' in drawn, "a parent cannot reach the child that implements it"

    # A closed child is a debt already paid, the cut `_edges` makes on a blocker.
    doc["units"] = [row for row in doc["units"] if row["id"] != "basicly-rn0o.3"]
    assert board_record._family(doc, QUIET)[1] == ()


def test_a_record_with_no_lane_says_so_and_prints_the_producers_own_command(
    doc: dict[str, Any],
) -> None:
    """The engine's remedy text, copied. Both mutation directions are run on it."""
    drawn = page(doc, QUIET)
    assert drawn is not None
    assert board_record.NO_LANE in drawn
    assert f"basicly loop advance {QUIET}" in drawn

    # Reworded: the page must move with the producer, which a page composing its own copy of
    # the same string would not.
    row = next(held for held in doc["detail"] if held["id"] == QUIET)
    row["next_command"] = "basicly loop advance --dry-run " + QUIET
    assert "basicly loop advance --dry-run " + QUIET in shown(doc, QUIET)

    # Withheld: absent, never a command this consumer went and built.
    del row["next_command"]
    withheld = shown(doc, QUIET)
    assert "basicly loop advance" not in withheld
    assert "no next command" in withheld


def test_the_running_lane_names_its_agent_model_start_spend_and_last_word(
    with_lane: dict[str, Any],
) -> None:
    """The five figures a developer opens a running record for, plus the snapshot's age."""
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
    """The mutation that matters most here: drop the section and read what the page says."""
    del doc["detail"]
    drawn = page(doc, QUIET)
    assert drawn is not None
    assert drawn.count("not emitted by this producer") >= 1
    assert "none bound" not in drawn, "an unreported binding is drawn as no binding"
    assert "not approved" not in drawn, "an unreported checkpoint is drawn as outstanding"
    assert "basicly loop advance" not in drawn


def test_no_page_is_drawn_for_a_record_the_document_never_listed(doc: dict[str, Any]) -> None:
    """A page of absent fields would read as a record the producer knows nothing about."""
    assert page(doc, "basicly-nothing") is None
    assert page(doc, "../../etc/passwd") is None


@pytest.mark.parametrize(
    "ident", ["../escape", "/absolute", "sub/dir", ".hidden", "with space", ""]
)
def test_an_id_that_cannot_be_a_file_name_is_refused(ident: str) -> None:
    """The ids are a producer's, so one of them may name a path outside the output directory."""
    assert not board_record.writable(ident)


def test_every_region_that_prints_an_id_links_it_to_that_records_page(
    with_lane: dict[str, Any],
) -> None:
    """Criterion five, read off the wall's own bytes.

    Next up, running now, parked, claimed, events and asks. The ask's own record gets a unit
    row here, which is the shape production always has: `board_facts._hide_unanswerable`
    drops an ask whose record the document no longer lists.
    """
    asked = with_lane["asks"][0]["issue"]
    with_lane["units"] = [
        *with_lane["units"],
        {"id": "basicly-parked1", "status": "deferred", "title": "parked work", "priority": "P3"},
        {"id": asked, "status": "open", "title": "the record an ask names", "priority": "P1"},
    ]
    drawn = wall(with_lane)
    for ident in (QUIET, RUNNING, "basicly-parked1"):
        assert f'href="record/{ident}.html"' in drawn, f"{ident} is printed but not linked"
    # The event ticker and the ask band name a record each; both must be links too. Counted
    # per region rather than against the unit count: the ready list is bounded by the height
    # the wall has, so most units are deliberately not printed at all.
    for row in (with_lane["events"][-1], with_lane["asks"][0]):
        assert f'href="record/{row["issue"]}.html"' in drawn


def test_an_id_with_no_page_behind_it_is_printed_and_not_linked(doc: dict[str, Any]) -> None:
    """The control on the rule above, and the case that forced it.

    A marker names the record it was written on, and that record may since have closed - so
    the event strip can name an id `units` does not list. A page is drawn from a unit row, so
    linking one of those would put a link to a missing file on the wall.
    """
    doc["events"][-1] = {**doc["events"][-1], "issue": "basicly-closed9"}
    drawn = wall(doc)
    assert "basicly-closed9" in drawn, "the event strip dropped the record it names"
    assert 'href="record/basicly-closed9.html"' not in drawn


def test_a_clipped_row_carries_the_untruncated_title(doc: dict[str, Any]) -> None:
    """Criterion six: the reader recovers the whole title without leaving the board."""
    long_title = "a title far longer than the ready column can hold " * 3
    row = next(unit for unit in doc["units"] if unit["id"] == QUIET)
    row["title"] = long_title
    drawn = wall(doc)
    assert f'title="{long_title}"' in drawn
    assert f"{long_title}</td>" not in drawn, "the column is not clipping at all"


def test_the_rework_count_is_the_engines_own_count(work_repo: Path) -> None:
    """The producer counts rework off parsed markers; `policy` counts it off the log.

    Two paths over one corpus, which is the only thing that keeps the marker header this
    module reads bound to the one `policy` writes.
    """
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
    """The demonstration's shape: the href on the wall is a file on disk beside it."""
    doc = document("wall-v1.json")
    out = tmp_path / "board.html"
    out.write_text(wall(doc), encoding="utf-8")
    written, refused = board_cli._write_records(doc, _verdict(doc), out, STAMPED)
    assert written == len(doc["units"])
    assert refused == 0
    landed = out.parent / board_record.href(QUIET)
    assert landed.is_file(), "the wall links to a page nobody wrote"
    assert QUIET in landed.read_text(encoding="utf-8")
    # The back link is the page it was reached from, which only this caller knows.
    assert 'href="../board.html"' in landed.read_text(encoding="utf-8")


def test_the_server_answers_from_the_document_it_is_serving() -> None:
    """The route's own lookup: an id in the served document, and one that is not.

    No filesystem is reached under this route at all, which is what makes a traversal
    unanswerable rather than merely refused. The two URL spellings are asserted over the
    wire in `tests/test_board_serve.py`, where the listener harness lives.
    """
    board = board_serve.Board(REPO_ROOT, build=dict)
    board._served = json.dumps(document("wall-v1.json")).encode("utf-8")

    drawn = board.record(QUIET, STAMPED)
    assert drawn is not None
    assert QUIET.encode() in drawn
    assert board.record("basicly-nothing", STAMPED) is None
    assert board.record("../../etc/passwd", STAMPED) is None
    assert f"/{board_record.HREF_DIR}/" == board_serve.RECORD_ROUTE


def test_the_page_dates_itself_against_the_snapshot_it_was_drawn_from(doc: dict[str, Any]) -> None:
    """Criterion two's last clause.

    A stale field read as a live one is the failure mode this whole board is written against,
    so the record page carries the document's age exactly as the wall does.
    """
    assert doc["generated_at"] in shown(doc, QUIET)
    later = datetime(2026, 8, 22, 16, 42, 52, tzinfo=UTC)
    filled = board_record.context(doc, _verdict(doc), QUIET, later)
    assert filled is not None
    assert "24h 0m ago" in board_render.render_record(filled, TEMPLATES)

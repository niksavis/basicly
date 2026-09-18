from __future__ import annotations

import json
import re
from pathlib import Path

import pytest

from tests.kit_deployment_helpers import CLOCK, KIT_RELATIVE, REPO_ROOT, _load, events

cli = _load(REPO_ROOT / KIT_RELATIVE / "cli.py", "kit_board_test_cli")
board = _load(REPO_ROOT / KIT_RELATIVE / "board.py", "kit_board_test_board")

TRIGGER = "When a record is picked up, I want its criteria present, so I can verify against them."


@pytest.fixture
def ledger(tmp_path: Path) -> Path:
    directory = tmp_path / "ledger"
    directory.mkdir()
    return directory


def _append(ledger: Path, drafts) -> None:
    events.append(ledger, drafts, actor="test", clock=lambda: CLOCK)


def _record(ledger: Path, record: str, *, title: str, shaped: bool = False) -> None:
    fields = {"title": title}
    if shaped:
        fields.update({
            "description": TRIGGER,
            "acceptance_criteria": "it is checked",
            "requirements": "it is portable",
        })
    _append(
        ledger,
        [
            events.Draft(record, events.KIND_CREATED, fields),
            events.Draft(record, events.KIND_STATUS, {"status": "open"}),
        ],
    )


def _blocks(ledger: Path, dependent: str, target: str) -> None:
    _append(
        ledger,
        [
            events.Draft(
                dependent,
                events.KIND_EDGE,
                {"from": dependent, "to": target, "type": "blocks"},
            )
        ],
    )


@pytest.fixture
def populated(ledger: Path) -> Path:
    _record(ledger, "demo-aa11", title="the blocked one")
    _record(ledger, "demo-bb22", title="the blocker", shaped=True)
    _blocks(ledger, "demo-aa11", "demo-bb22")
    return ledger


def test_an_empty_ledger_renders_a_page_that_says_so(
    ledger: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = ledger.parent / "page.html"

    assert cli.main(["board", str(ledger), "--out", str(out)]) == cli.EXIT_OK
    capsys.readouterr()

    assert board.EMPTY_LEDGER in out.read_text(encoding="utf-8")


def test_the_page_links_no_asset_and_reaches_no_network(populated: Path) -> None:
    text = board.page(populated)

    for forbidden in ("<script", "<link", "http://", "https://", "@import", "url(/", "url(."):
        assert forbidden not in text, (
            f"{forbidden!r} is in the page; it must open with no other file beside it"
        )
    assert "<style>" in text, "the control: the page does carry its own inline style"
    assert "url(#a)" in text, (
        "the control: the only url() is the SVG's own marker reference, which is why the "
        "check above cannot simply forbid url("
    )


def test_the_page_carries_every_section(populated: Path) -> None:
    sections = re.findall(r"<h2>([^<]+)</h2>", board.page(populated))

    assert sections == ["Ready", "Blocked", "Dependencies", "Every record"]


def test_the_ready_row_and_the_blocked_row_agree_with_the_queries(populated: Path) -> None:
    text = board.page(populated)

    assert "demo-bb22" in text
    assert "demo-aa11" in text
    blocked = text[text.index("<h2>Blocked</h2>") : text.index("<h2>Dependencies</h2>")]
    assert "demo-aa11" in blocked
    assert "demo-bb22" in blocked, "the blocked row must name what holds it"


def test_a_record_that_owes_a_section_is_marked_and_a_shaped_one_is_not(
    populated: Path,
) -> None:
    text = board.page(populated)
    every = text[text.index("<h2>Every record</h2>") :]

    rows = dict(
        re.findall(
            r"<code>(demo-[^<]+)</code></td><td>[^<]*</td><td>[^<]*</td><td>"
            r"<span class=\"(owed|none)\"",
            every,
        )
    )

    assert rows["demo-aa11"] == "owed"
    assert rows["demo-bb22"] == "none"


def test_every_record_carries_its_title_not_an_empty_cell(populated: Path) -> None:
    every = board.page(populated)
    every = every[every.index("<h2>Every record</h2>") :]

    assert "the blocked one" in every, (
        "the title lives under fields in a record dict, not at the top level; reading it "
        "from the top level rendered an empty column that only a screenshot caught"
    )
    assert "the blocker" in every


def test_the_graph_states_what_it_drew_against_what_the_ledger_holds(populated: Path) -> None:
    found = re.search(
        r"(\d+) of (\d+) record\(s\) and (\d+) of (\d+) blocking edge\(s\) drawn",
        board.page(populated),
    )

    assert found is not None
    drawn_nodes, total_nodes, drawn_edges, total_edges = (int(one) for one in found.groups())
    assert (drawn_nodes, drawn_edges) == (2, 1)
    assert total_nodes == 2
    assert total_edges == 1


def _fan_in(ledger: Path, size: int) -> None:
    for index in range(size):
        _record(ledger, f"demo-{index:04d}", title=f"record {index}")
    for index in range(1, size):
        _blocks(ledger, f"demo-{index:04d}", "demo-0000")


def test_a_cluster_inside_the_cap_is_drawn_whole(ledger: Path) -> None:
    _fan_in(ledger, board.MAX_NODES)

    nodes, edges = board._dispatchable(ledger)

    assert len(nodes) == board.MAX_NODES
    assert len(edges) == board.MAX_NODES - 1, "every edge of a drawn cluster is drawn"


def test_a_cluster_over_the_cap_is_dropped_whole_rather_than_cut(ledger: Path) -> None:
    _fan_in(ledger, board.MAX_NODES + 4)

    nodes, edges = board._dispatchable(ledger)

    assert (nodes, edges) == ([], []), (
        "a partly drawn cluster would show a blocked record with no arrow into it, which "
        "reads as nothing open against it; dropping it whole is the honest failure"
    )
    note = re.search(r"(\d+) of (\d+) record\(s\) and (\d+) of (\d+) blocking", board.page(ledger))
    assert note is not None
    assert note.group(1) == "0", "the page must say it drew nothing rather than look empty"
    assert int(note.group(4)) == board.MAX_NODES + 3, "and must still name the edges it holds"
    assert len(board._waits_on(ledger)) == board.MAX_NODES + 3, (
        "the table carries every pair the drawing declined"
    )


def test_the_edge_table_lists_every_blocking_pair_the_graph_cannot_draw(
    populated: Path,
) -> None:
    assert board._waits_on(populated) == [("demo-aa11", ["demo-bb22"])]


def test_the_command_writes_the_file_and_reports_where(
    populated: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    out = populated.parent / "nested" / "page.html"

    assert cli.main(["board", str(populated), "--out", str(out)]) == cli.EXIT_OK

    assert json.loads(capsys.readouterr().out) == {"written": out.as_posix()}
    assert out.is_file()


def test_a_title_with_markup_is_escaped_rather_than_rendered(ledger: Path) -> None:
    _record(ledger, "demo-cc33", title="<script>alert(1)</script>")

    text = board.page(ledger)

    assert "<script>" not in text
    assert "&lt;script&gt;" in text

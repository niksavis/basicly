from __future__ import annotations

from pathlib import Path

from basicly import comment_rows, tracker
from tests import flipped_tracker


def _comment(repo: Path, record: str, text: str, kind: str = "") -> None:
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [kit.events.Draft(record, kind or kit.events.KIND_COMMENT, {comment_rows.TEXT_KEY: text})],
    )


def _rows(repo: Path) -> dict[str, list[dict]]:
    kit = tracker.kit(repo)
    return comment_rows.from_ledger(kit, flipped_tracker.ledger_events(repo))


def test_a_record_s_comments_come_back_oldest_first(tmp_path: Path) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    _comment(repo, "seam-1", "first")
    _comment(repo, "seam-1", "second")

    assert [row[comment_rows.TEXT_KEY] for row in _rows(repo)["seam-1"]] == ["first", "second"]


def test_a_row_carries_the_body_and_the_event_s_own_stamp(tmp_path: Path) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    _comment(repo, "seam-1", "[harness-policy] hello")

    row = _rows(repo)["seam-1"][0]
    event = next(
        e
        for e in flipped_tracker.ledger_events(repo)
        if e.kind == tracker.kit(repo).events.KIND_COMMENT
    )
    assert row == {
        comment_rows.TEXT_KEY: "[harness-policy] hello",
        comment_rows.STAMP_KEY: event.ts,
    }


def test_a_tombstoned_record_answers_empty(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    _comment(repo, "seam-1", "counted")
    _comment(repo, "seam-2", "kept")
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo), [kit.events.Draft("seam-1", kit.events.KIND_TOMBSTONE, {})]
    )

    rows = _rows(repo)
    assert "seam-1" not in rows
    assert [row[comment_rows.TEXT_KEY] for row in rows["seam-2"]] == ["kept"]


def _cut_comment(repo: Path, record: str) -> tuple[str, int]:
    kit = tracker.kit(repo)
    body = "[harness-artifact] kind=implementation-plan " + "y" * kit.events.MAX_TEXT_BYTES
    _comment(repo, record, body)
    return body, len(body.encode("utf-8"))


def test_a_row_whose_body_the_cap_cut_carries_both_of_the_cap_s_markers(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    _, whole = _cut_comment(repo, "seam-1")

    row = _rows(repo)["seam-1"][0]
    assert row[comment_rows.TRUNCATED_KEY] is True
    assert row[comment_rows.ORIGINAL_LENGTH_KEY] == whole
    assert len(row[comment_rows.TEXT_KEY].encode("utf-8")) < whole


def test_a_flag_with_no_length_beside_it_does_not_mark_the_row(tmp_path: Path) -> None:
    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [
            kit.events.Draft(
                "seam-1",
                kit.events.KIND_COMMENT,
                {comment_rows.TEXT_KEY: "short", comment_rows.TRUNCATED_KEY: True},
            )
        ],
    )

    assert comment_rows.TRUNCATED_KEY not in _rows(repo)["seam-1"][0]


def test_both_prose_spellings_render_as_rows_in_one_history(tmp_path: Path) -> None:

    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    _comment(repo, "seam-1", "written before", kind=kit.events.KIND_COMMENT)
    _comment(repo, "seam-1", "written after", kind=kit.events.KIND_NOTE)

    rows = _rows(repo)["seam-1"]

    assert [row[comment_rows.TEXT_KEY] for row in rows] == ["written before", "written after"]

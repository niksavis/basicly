"""The comment history both stores answer with, rendered into one row shape.

Split out of ``br`` with the module (`basicly-wpc8.1`). The rules are asserted against a
real kit and a real ledger rather than through the seam, because a rendering that got the
tombstone or the stamp wrong would still round-trip one comment written and read back —
which is all the seam-level tests need it to do.
"""

from __future__ import annotations

from pathlib import Path

from basicly import comment_rows, tracker
from tests import flipped_tracker


def _comment(repo: Path, record: str, text: str, kind: str = "") -> None:
    """Append one prose event, through the kit the renderer will be handed."""
    kit = tracker.kit(repo)
    kit.events.append(
        tracker.ledger_dir(repo),
        [kit.events.Draft(record, kind or kit.events.KIND_COMMENT, {comment_rows.TEXT_KEY: text})],
    )


def _rows(repo: Path) -> dict[str, list[dict]]:
    kit = tracker.kit(repo)
    return comment_rows.from_ledger(kit, flipped_tracker.ledger_events(repo))


def test_a_record_s_comments_come_back_oldest_first(tmp_path: Path) -> None:
    """The order `decisions` documents its per-bead read as, and `policy`'s clock needs."""
    repo = flipped_tracker.flipped_repo(tmp_path)
    _comment(repo, "seam-1", "first")
    _comment(repo, "seam-1", "second")

    assert [row[comment_rows.TEXT_KEY] for row in _rows(repo)["seam-1"]] == ["first", "second"]


def test_a_row_carries_the_body_and_the_event_s_own_stamp(tmp_path: Path) -> None:
    """The stamp is the ledger's, not this process's: an interval outlives the process."""
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
    """The two stores spell a deletion differently; this makes them agree.

    br answers a deleted record by not returning it. The ledger keeps the record and
    flags it, so a renderer that ignored the flag would serve markers for a bead somebody
    deleted — and a rework counter read off them counts work nobody is doing.
    """
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
    """Append one comment the cap must cut; return its marker prefix and its whole size."""
    kit = tracker.kit(repo)
    body = "[harness-artifact] kind=implementation-plan " + "y" * kit.events.MAX_TEXT_BYTES
    _comment(repo, record, body)
    return body, len(body.encode("utf-8"))


def test_a_row_whose_body_the_cap_cut_carries_both_of_the_cap_s_markers(tmp_path: Path) -> None:
    """The evidence a refusing reader needs, which lived only on the event until now.

    Measured 2026-08-18 over the committed ledger: 23 of the 47 artifact record/kind
    pairs are stored cut, and every one of them carries the original length here. A row
    that dropped it left the consumer quoting a JSON fragment as if it were malformed.
    """
    repo = flipped_tracker.flipped_repo(tmp_path)
    _, whole = _cut_comment(repo, "seam-1")

    row = _rows(repo)["seam-1"][0]
    assert row[comment_rows.TRUNCATED_KEY] is True
    assert row[comment_rows.ORIGINAL_LENGTH_KEY] == whole
    assert len(row[comment_rows.TEXT_KEY].encode("utf-8")) < whole


def test_a_flag_with_no_length_beside_it_does_not_mark_the_row(tmp_path: Path) -> None:
    """Both markers or neither: a cut named without its size is a reason nobody can act on."""
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
    """A reader keying on `note` alone would drop the markers on every older event.

    2,667 of this repository's own ledger events are `comment` and the log is never
    rewritten, so the alias is what `decision_marker` and `artifact_record` read their
    markers through (basicly-vkh0.30).
    """
    repo = flipped_tracker.flipped_repo(tmp_path)
    kit = tracker.kit(repo)
    _comment(repo, "seam-1", "written before", kind=kit.events.KIND_COMMENT)
    _comment(repo, "seam-1", "written after", kind=kit.events.KIND_NOTE)

    rows = _rows(repo)["seam-1"]

    assert [row[comment_rows.TEXT_KEY] for row in rows] == ["written before", "written after"]

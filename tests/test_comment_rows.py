"""The comment history both stores answer with, rendered into one row shape.

Split out of ``br`` with the module (`basicly-wpc8.1`). The rules are asserted against a
real kit and a real ledger rather than through the seam, because a rendering that got the
tombstone or the stamp wrong would still round-trip one comment written and read back —
which is all the seam-level tests need it to do.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from basicly import br, comment_rows
from tests import flipped_tracker


def _comment(repo: Path, record: str, text: str) -> None:
    """Append one ``comment`` event, through the kit the renderer will be handed."""
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo),
        [kit.events.Draft(record, kit.events.KIND_COMMENT, {comment_rows.TEXT_KEY: text})],
    )


def _rows(repo: Path) -> dict[str, list[dict]]:
    kit = br.kit(repo)
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
        e for e in flipped_tracker.ledger_events(repo) if e.kind == br.kit(repo).events.KIND_COMMENT
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
    kit = br.kit(repo)
    kit.events.append(
        br.ledger_dir(repo), [kit.events.Draft("seam-1", kit.events.KIND_TOMBSTONE, {})]
    )

    rows = _rows(repo)
    assert "seam-1" not in rows
    assert [row[comment_rows.TEXT_KEY] for row in rows["seam-2"]] == ["kept"]


def test_a_reply_that_is_not_an_array_raises_rather_than_reading_as_no_markers() -> None:
    """An unreadable tracker must not answer "nothing is blocking" (both shapes)."""
    with pytest.raises(RuntimeError, match="no usable JSON"):
        comment_rows.from_br_reply("not json", "seam-1")
    with pytest.raises(RuntimeError, match="not an array"):
        comment_rows.from_br_reply(json.dumps({"results": []}), "seam-1")


def test_a_reply_s_rows_survive_a_non_row_entry() -> None:
    """The reply's array is taken row by row: one unusable entry is not the history."""
    reply = json.dumps([{"text": "kept", "created_at": "2026-08-17T00:00:00Z"}, "junk"])

    assert comment_rows.from_br_reply(reply, "seam-1") == [
        {"text": "kept", "created_at": "2026-08-17T00:00:00Z"}
    ]

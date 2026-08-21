"""Tests for the single tracker adapter seam (src/basicly/tracker.py).

The spawn's own tests left with the spawn (basicly-vkh0.42.7). What stays is the seam's
contract and the tree guards that keep it the only copy.
"""

from __future__ import annotations

from pathlib import Path

from basicly import tracker

# comment-density-waiver: cohesion: three tests over ~200 tokens of code, so the share is set by the
# member count and not by narration — the same shape as `label_source`. What is left is
# the incident behind the tree guard, which is the one fact a reader cannot get from the
# assertion. Two thirds of this module left with the subprocess it described.

# --- the whole-tracker record read (basicly-kjc5.50) --------------------------


def test_export_comment_texts_reads_only_well_formed_comments() -> None:
    """Comments carry the evidence; a malformed row is ignored."""
    record = {
        "id": "b-1",
        "comments": [{"text": "first"}, {"author": "niksa"}, "not a row", {"text": 7}],
    }
    assert tracker.export_comment_texts(record) == ["first"]
    assert tracker.export_comment_texts({"id": "b-2"}) == []


def test_all_records_is_empty_without_a_ledger(tmp_path: Path) -> None:
    """No tracker, no records: every consumer here is evidence, never a gate."""
    assert tracker.all_records(tmp_path) == []


# --- The one record read seam (basicly-tcmy.14) -------------------------------
#
# What the seam answers is `test_br_seam.py`'s; what stays here keeps it in one place.


def test_no_module_outside_the_seam_unwraps_a_record_itself() -> None:
    """The rule this bead exists to hold, checked against the tree rather than by eye.

    Eleven call sites across eight modules each wrote this expression out, in two
    variants that disagreed on the empty-array case. `tracker.py` is the one place allowed to
    know the shape; a twelfth copy anywhere else re-acquires the split, which is the same
    reason :func:`tracker.dependency_edge` exists.

    Matched on the unwrap *expression*, not on ``isinstance(data, list)`` alone: a plain
    list check is an ordinary shape guard on any payload, and `policy._finding_members`
    is one over a finding set. Banning that would be banning JSON.
    """
    unwrap = "data[0] if isinstance(data, list)"
    root = Path(__file__).parent.parent / "src" / "basicly"
    offenders = [
        path.name
        for path in sorted(root.glob("*.py"))
        if path.name != "tracker.py" and unwrap in path.read_text(encoding="utf-8")
    ]
    assert offenders == []

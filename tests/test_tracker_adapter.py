from __future__ import annotations

from pathlib import Path

from basicly import tracker


def test_export_comment_texts_reads_only_well_formed_comments() -> None:
    record = {
        "id": "b-1",
        "comments": [{"text": "first"}, {"author": "niksa"}, "not a row", {"text": 7}],
    }
    assert tracker.export_comment_texts(record) == ["first"]
    assert tracker.export_comment_texts({"id": "b-2"}) == []


def test_all_records_is_empty_without_a_ledger(tmp_path: Path) -> None:
    assert tracker.all_records(tmp_path) == []


def test_no_module_outside_the_seam_unwraps_a_record_itself() -> None:

    unwrap = "data[0] if isinstance(data, list)"
    root = Path(__file__).parent.parent / "src" / "basicly"
    offenders = [
        path.name
        for path in sorted(root.glob("*.py"))
        if path.name != "tracker.py" and unwrap in path.read_text(encoding="utf-8")
    ]
    assert offenders == []

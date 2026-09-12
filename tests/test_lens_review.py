from __future__ import annotations

from pathlib import Path

import pytest

from basicly import lens_review, roles


@pytest.fixture
def comments(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lens_review.tracker, "add_comment", lambda _r, issue, body: written.append((issue, body))
    )
    return written


def test_each_lens_lands_as_its_own_marker_carrying_its_own_name(
    comments: list[tuple[str, str]], tmp_path: Path
) -> None:

    lens_review.record(tmp_path, "i", "correctness", "off-by-one at parse.py:12 (major)")
    lens_review.record(tmp_path, "i", "security", "shell injection at run.py:4 (blocker)")

    assert [issue for issue, _ in comments] == ["i", "i"]
    first, second = (body for _, body in comments)
    assert first.startswith(f"{lens_review.MARKER} lens=correctness\n")
    assert second.startswith(f"{lens_review.MARKER} lens=security\n")
    assert "security" not in first
    assert "correctness" not in second


def test_an_empty_reply_records_nothing_rather_than_an_empty_lens(
    comments: list[tuple[str, str]], tmp_path: Path
) -> None:

    lens_review.record(tmp_path, "i", "security", "   \n  ")

    assert comments == []

    lens_review.record(tmp_path, "i", "security", "Nothing on this axis.")

    assert len(comments) == 1


def test_a_second_review_of_one_lens_is_kept_beside_the_first(
    comments: list[tuple[str, str]], tmp_path: Path
) -> None:

    lens_review.record(tmp_path, "i", "correctness", "same finding")
    lens_review.record(tmp_path, "i", "correctness", "same finding")

    assert len(comments) == 2


@pytest.fixture
def recorded(monkeypatch: pytest.MonkeyPatch) -> list[dict]:
    rows: list[dict] = []
    monkeypatch.setattr(lens_review.tracker, "try_read_comments", lambda _r, _i: list(rows))
    return rows


def test_the_read_back_shape_is_one_entry_per_lens_in_vocabulary_order(
    recorded: list[dict], tmp_path: Path
) -> None:

    recorded += [
        {"text": f"{lens_review.MARKER} lens=security\nshell injection at run.py:4"},
        {"text": f"{lens_review.MARKER} lens=correctness\noff-by-one at parse.py:12"},
        {"text": "[harness-policy] rework gate=verify"},
    ]

    read = lens_review.latest_per_lens(tmp_path, "i")

    assert [entry.lens for entry in read] == list(roles.REVIEW_LENSES)
    assert {entry.lens: entry.findings for entry in read} == {
        "correctness": "off-by-one at parse.py:12",
        "security": "shell injection at run.py:4",
    }


def test_a_lens_that_recorded_nothing_comes_back_empty_rather_than_absent(
    recorded: list[dict], tmp_path: Path
) -> None:
    recorded.append({"text": f"{lens_review.MARKER} lens=correctness\nonly this axis answered"})

    read = lens_review.latest_per_lens(tmp_path, "i")

    assert [entry.lens for entry in read] == list(roles.REVIEW_LENSES)
    assert [entry.findings for entry in read] == ["only this axis answered", ""]


def test_the_latest_review_of_a_lens_is_the_one_a_repair_is_briefed_with(
    recorded: list[dict], tmp_path: Path
) -> None:
    recorded += [
        {"text": f"{lens_review.MARKER} lens=correctness\nthe tree the first attempt left"},
        {"text": f"{lens_review.MARKER} lens=correctness\nthe tree the repair left"},
    ]

    read = lens_review.latest_per_lens(tmp_path, "i")

    assert read[0] == lens_review.LensFindings("correctness", "the tree the repair left")


def test_a_marker_naming_a_lens_outside_the_vocabulary_is_dropped(
    recorded: list[dict], tmp_path: Path
) -> None:

    recorded += [
        {"text": f"{lens_review.MARKER} lens=performance\nan axis nobody declares"},
        {"text": f"{lens_review.MARKER} lens=security\na declared axis"},
    ]

    read = lens_review.latest_per_lens(tmp_path, "i")

    assert "performance" not in {entry.lens for entry in read}
    assert {entry.lens: entry.findings for entry in read}["security"] == "a declared axis"


def test_a_store_that_cannot_answer_costs_the_findings_and_never_a_verdict(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:

    monkeypatch.setattr(lens_review.tracker, "try_read_comments", lambda _r, _i: [])

    read = lens_review.latest_per_lens(tmp_path, "i")

    assert [entry.findings for entry in read] == [""] * len(roles.REVIEW_LENSES)

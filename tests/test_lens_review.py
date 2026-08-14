"""One lens review's findings, as recorded on the unit that was reviewed (basicly-feje).

The rule under test is §6.4's: lens output is reported per lens and never merged into one
ranked list. These assert it on the recorded form, which is where the guarantee lives —
the prompt only instructs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from basicly import lens_review


@pytest.fixture
def comments(monkeypatch: pytest.MonkeyPatch) -> list[tuple[str, str]]:
    """Capture what reaches the tracker; these tests run outside a `br` store."""
    written: list[tuple[str, str]] = []
    monkeypatch.setattr(
        lens_review.br, "add_comment", lambda _r, issue, body: written.append((issue, body))
    )
    return written


def test_each_lens_lands_as_its_own_marker_carrying_its_own_name(
    comments: list[tuple[str, str]], tmp_path: Path
) -> None:
    """Two lenses, two records, and neither one names the other.

    The no-rerank rule holds by construction rather than by instruction: there is no
    call shape here that takes two lenses, so nothing can rank one against the other.
    """
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
    """A blank marker would read as a lens that ran and was clean.

    A reviewer with no finding on its axis says so in one line, which is text and is
    recorded like any other; a reviewer that answered nothing at all is a dispatch that
    failed, and the two must not look the same to the next reader.
    """
    lens_review.record(tmp_path, "i", "security", "   \n  ")

    assert comments == []

    lens_review.record(tmp_path, "i", "security", "Nothing on this axis.")

    assert len(comments) == 1


def test_a_second_review_of_one_lens_is_kept_beside_the_first(
    comments: list[tuple[str, str]], tmp_path: Path
) -> None:
    """Re-entering VALIDATE re-dispatches, and two answers are about two trees.

    Collapsing them on equality would be right for an idempotent marker like
    ``[harness-artifact]`` and wrong here: the later review is the one that saw the
    repair, and it is the one a dedupe would drop when a lens found the same thing twice.
    """
    lens_review.record(tmp_path, "i", "correctness", "same finding")
    lens_review.record(tmp_path, "i", "correctness", "same finding")

    assert len(comments) == 2

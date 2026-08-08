"""Tests for the conflation key two spellings must share (basicly-m4zv.2).

Written against word pairs alone, which is the point of the module being separate
from the ranker: a stemming rule is arguable from "these two spellings must land on
one key" without a corpus, a score or a catalog anywhere in the fixture.
"""

from __future__ import annotations

import pytest

from basicly import stemmer


@pytest.mark.parametrize(
    ("plural", "singular"),
    [
        ("branches", "branch"),
        ("boxes", "box"),
        ("passes", "pass"),
        ("sources", "source"),
        ("files", "file"),
        ("uses", "use"),
        ("ponies", "poni"),
    ],
)
def test_a_plural_and_its_singular_share_one_stem(plural: str, singular: str) -> None:
    """Conflation is the whole job: both spellings must land on one key.

    ``branches`` is the regression. A bare "-s" strip leaves ``branche``, which
    never meets ``branch``, and the prompt "what branch am I on" could not reach
    an entry whose description says "branches" — found by this gate running on
    the real catalog, not by inspection.
    """
    assert stemmer.stem(plural) == stemmer.stem(singular)


@pytest.mark.parametrize(
    ("inflected", "base"),
    [("running", "run"), ("hoping", "hope"), ("rendered", "render"), ("quickly", "quick")],
)
def test_an_inflected_form_stems_to_its_base(inflected: str, base: str) -> None:
    """A verb's inflections must reach the base form a description uses."""
    assert stemmer.stem(inflected) == stemmer.stem(base)


def test_stop_words_and_single_characters_are_dropped() -> None:
    """Only terms that can discriminate between entries survive tokenization."""
    assert stemmer.tokenize("is it a the of x json") == ["json"]


def test_a_hyphenated_compound_splits_into_its_words() -> None:
    """A prompt saying "pre-commit" must reach an entry whose description says "commit"."""
    assert "commit" in stemmer.tokenize("the pre-commit hook")

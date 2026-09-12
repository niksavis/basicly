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

    assert stemmer.stem(plural) == stemmer.stem(singular)


@pytest.mark.parametrize(
    ("inflected", "base"),
    [("running", "run"), ("hoping", "hope"), ("rendered", "render"), ("quickly", "quick")],
)
def test_an_inflected_form_stems_to_its_base(inflected: str, base: str) -> None:
    assert stemmer.stem(inflected) == stemmer.stem(base)


def test_stop_words_and_single_characters_are_dropped() -> None:
    assert stemmer.tokenize("is it a the of x json") == ["json"]


def test_a_hyphenated_compound_splits_into_its_words() -> None:
    assert "commit" in stemmer.tokenize("the pre-commit hook")

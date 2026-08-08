"""Conflation: the key two differently-spelled words have to share to be comparable.

One responsibility, and it is a word's key. A user types "which branches am I on" and
a description says "branch"; unless both reduce to the same token, every ranker above
this scores them as unrelated. :func:`stem` answers *what key does this word have* and
:func:`tokenize` answers it for a whole string; nothing here compares two texts or
ranks anything.

The rules are a deliberately conservative subset of Porter's step 1 — plurals,
``-ed``/``-ing``, ``-ly`` — rather than the full algorithm: those are the endings that
actually separate a user's phrasing from a description's, and every derivational rule
beyond them buys less conflation than it risks. The table below *is* the
specification; this is our stemmer, not a claim about someone else's.

Split out of ``catalog_routing`` when the module-size ratchet caught that module
growing. The boundary is *vocabulary* against *scoring*: this module knows nothing of
corpora, TF-IDF weights, eval cases or catalogs, which is why it needs no import back
into the module it came from and why a stemming rule can be argued about from a list
of word pairs alone.
"""

from __future__ import annotations

import re

# Word characters only: "tf-idf" becomes "tf" + "idf" and "pre-commit" becomes
# "pre" + "commit", so a hyphenated compound in a description still matches the
# bare word a user types.
_TOKEN_RE = re.compile(r"[a-z0-9]+")

_VOWELS = frozenset("aeiou")

# Pure function words. Deliberately short: IDF already flattens a term that
# appears in every description, so the stop list only has to remove words that
# are frequent *and* arbitrary. Directional particles ("up", "out", "over") are
# kept — they carry real signal in phrases like "set up" and "roll out".
STOPWORDS = frozenset({
    "a",
    "an",
    "and",
    "are",
    "as",
    "at",
    "be",
    "been",
    "but",
    "by",
    "can",
    "do",
    "does",
    "for",
    "from",
    "has",
    "have",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "me",
    "my",
    "no",
    "not",
    "of",
    "on",
    "or",
    "so",
    "than",
    "that",
    "the",
    "their",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "was",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
})


def _consonant_at(word: str, index: int) -> bool:
    """True when ``word[index]`` is a consonant, treating 'y' positionally."""
    char = word[index]
    if char in _VOWELS:
        return False
    if char != "y":
        return True
    # A leading 'y' is a consonant; elsewhere it is a consonant only after a
    # vowel ("happy" ends vowel-consonant, "cry" ends consonant-vowel).
    return index == 0 or not _consonant_at(word, index - 1)


def _measure(word: str) -> int:
    """Porter's *m*: the number of vowel-then-consonant transitions in ``word``.

    ``m`` is what separates a suffix that is genuinely attached to a stem from
    one that is merely the end of a short word — "feed" keeps its "ee" because
    the stem before it has ``m == 0``, while "agreed" loses one because its does
    not.
    """
    pattern = "".join("c" if _consonant_at(word, i) else "v" for i in range(len(word)))
    return pattern.count("vc")


def _has_vowel(word: str) -> bool:
    return any(not _consonant_at(word, i) for i in range(len(word)))


def _ends_double_consonant(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _consonant_at(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:
    """True when ``word`` ends consonant-vowel-consonant, last not w/x/y.

    The shape that needs a silent "e" restored after stripping: "hop" from
    "hoping" wants "hope", while "hopping" undoubles to "hop" and stays there.
    """
    if len(word) < 3:
        return False
    last = len(word) - 1
    return (
        _consonant_at(word, last)
        and not _consonant_at(word, last - 1)
        and _consonant_at(word, last - 2)
        and word[last] not in "wxy"
    )


def _restore(stem_text: str) -> str:
    """Repair the stem left by an ``-ed``/``-ing`` strip.

    Three repairs, in order: a stem ending "at"/"bl"/"iz" regains its "e"
    ("conflat" -> "conflate"); a doubled final consonant that is not l/s/z is
    undoubled ("hopp" -> "hop"); a short consonant-vowel-consonant stem regains
    its "e" ("hop" from "hoping" -> "hope").
    """
    if stem_text.endswith(("at", "bl", "iz")):
        return stem_text + "e"
    if _ends_double_consonant(stem_text) and stem_text[-1] not in "lsz":
        return stem_text[:-1]
    if _measure(stem_text) == 1 and _ends_cvc(stem_text):
        return stem_text + "e"
    return stem_text


def stem(word: str) -> str:
    """Reduce ``word`` to its conflation key (see the module docstring).

    Conservative by construction: plurals, ``-ed``/``-ing`` and ``-ly`` only.
    Words of three characters or fewer are returned unchanged — there is no
    stem left to find and stripping one turns "ads" into "ad" but "was" into
    "wa".
    """
    if len(word) <= 3:
        return word

    # Plurals. The "-es" rule is the one worth stating: English adds a full
    # "es" after a sibilant, so a bare "-s" strip leaves "branches" as
    # "branche" and it never meets "branch" — which is exactly how "which
    # branch am I on" failed to reach `tool-git`. The stem-length guard keeps
    # it off short words where "es" is not a plural marker ("uses" -> "use",
    # not "us").
    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-3] + "i"
    elif word.endswith("es") and len(word) >= 5 and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us")):
        word = word[:-1]

    # Past and progressive.
    if word.endswith("eed"):
        if _measure(word[:-3]) > 0:
            word = word[:-1]
    else:
        for suffix in ("ed", "ing"):
            if word.endswith(suffix) and _has_vowel(word[: -len(suffix)]):
                word = _restore(word[: -len(suffix)])
                break

    # Adverbial.
    if len(word) > 4 and word.endswith("ly") and _has_vowel(word[:-2]):
        word = word[:-2]

    return word


def tokenize(text: str) -> list[str]:
    """Lowercase, split on word characters, drop stop words, stem what is left."""
    return [
        stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if token not in STOPWORDS and len(token) > 1
    ]

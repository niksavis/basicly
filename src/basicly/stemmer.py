from __future__ import annotations

import re

_TOKEN_RE = re.compile(r"[a-z0-9]+")

_VOWELS = frozenset("aeiou")

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
    char = word[index]
    if char in _VOWELS:
        return False
    if char != "y":
        return True
    return index == 0 or not _consonant_at(word, index - 1)


def _measure(word: str) -> int:

    pattern = "".join("c" if _consonant_at(word, i) else "v" for i in range(len(word)))
    return pattern.count("vc")


def _has_vowel(word: str) -> bool:
    return any(not _consonant_at(word, i) for i in range(len(word)))


def _ends_double_consonant(word: str) -> bool:
    return len(word) >= 2 and word[-1] == word[-2] and _consonant_at(word, len(word) - 1)


def _ends_cvc(word: str) -> bool:

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

    if stem_text.endswith(("at", "bl", "iz")):
        return stem_text + "e"
    if _ends_double_consonant(stem_text) and stem_text[-1] not in "lsz":
        return stem_text[:-1]
    if _measure(stem_text) == 1 and _ends_cvc(stem_text):
        return stem_text + "e"
    return stem_text


def stem(word: str) -> str:

    if len(word) <= 3:
        return word

    if word.endswith("sses"):
        word = word[:-2]
    elif word.endswith("ies"):
        word = word[:-3] + "i"
    elif word.endswith("es") and len(word) >= 5 and word[:-2].endswith(("s", "x", "z", "ch", "sh")):
        word = word[:-2]
    elif word.endswith("s") and not word.endswith(("ss", "us")):
        word = word[:-1]

    if word.endswith("eed"):
        if _measure(word[:-3]) > 0:
            word = word[:-1]
    else:
        for suffix in ("ed", "ing"):
            if word.endswith(suffix) and _has_vowel(word[: -len(suffix)]):
                word = _restore(word[: -len(suffix)])
                break

    if len(word) > 4 and word.endswith("ly") and _has_vowel(word[:-2]):
        word = word[:-2]

    return word


def tokenize(text: str) -> list[str]:
    return [
        stem(token)
        for token in _TOKEN_RE.findall(text.lower())
        if token not in STOPWORDS and len(token) > 1
    ]

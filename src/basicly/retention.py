from __future__ import annotations

import math
import re
from dataclasses import dataclass
from itertools import pairwise
from pathlib import Path

RETAINED_THRESHOLD = 0.5

_STOPWORDS = frozenset((
    "a",
    "again",
    "all",
    "always",
    "an",
    "and",
    "any",
    "are",
    "as",
    "at",
    "be",
    "been",
    "being",
    "but",
    "by",
    "can",
    "could",
    "did",
    "do",
    "does",
    "doing",
    "down",
    "each",
    "else",
    "every",
    "for",
    "from",
    "further",
    "he",
    "her",
    "here",
    "his",
    "how",
    "i",
    "if",
    "in",
    "into",
    "is",
    "it",
    "its",
    "may",
    "me",
    "might",
    "must",
    "my",
    "never",
    "no",
    "nor",
    "not",
    "of",
    "off",
    "on",
    "once",
    "only",
    "or",
    "our",
    "out",
    "over",
    "own",
    "same",
    "shall",
    "she",
    "should",
    "so",
    "some",
    "such",
    "than",
    "that",
    "the",
    "their",
    "them",
    "then",
    "there",
    "these",
    "they",
    "this",
    "those",
    "to",
    "too",
    "under",
    "up",
    "us",
    "very",
    "was",
    "we",
    "were",
    "what",
    "when",
    "where",
    "which",
    "while",
    "who",
    "whom",
    "why",
    "will",
    "with",
    "would",
    "you",
    "your",
    "yours",
))

_MARKDOWN = re.compile(r"[`*_~\[\]()#>|]")
_SPLIT = re.compile(r"[/\\,;:]+")
_WORD = re.compile(r"[a-z0-9][a-z0-9.-]*")
_SUFFIXES = ("ings", "ing", "edly", "ied", "ies", "ed", "es", "ly", "s")
_KEEP_WHOLE = frozenset({"is", "its", "as", "was", "does", "gates", "notes", "less"})


_CLAUSE = re.compile(r"[;\u2014:]")


@dataclass(frozen=True)
class Rule:
    rule_id: str
    section: str
    position: int
    text: str

    @property
    def words(self) -> frozenset[str]:
        return content_words(self.text)

    @property
    def imperative(self) -> str:
        return _CLAUSE.split(self.text, 1)[0].strip()

    @property
    def imperative_words(self) -> frozenset[str]:
        words = content_words(self.imperative)
        return words or self.words


@dataclass(frozen=True)
class Match:
    rule: Rule
    score: float
    evidence: str

    @property
    def retained(self) -> bool:
        return self.score >= RETAINED_THRESHOLD


@dataclass(frozen=True)
class Report:
    matches: tuple[Match, ...]
    response_lines: int

    @property
    def total(self) -> int:
        return len(self.matches)

    @property
    def retained(self) -> int:
        return sum(1 for match in self.matches if match.retained)

    @property
    def rate(self) -> float:
        return self.retained / self.total if self.total else 0.0

    def by_section(self) -> dict[str, tuple[int, int]]:
        counts: dict[str, list[int]] = {}
        for match in self.matches:
            entry = counts.setdefault(match.rule.section, [0, 0])
            entry[1] += 1
            if match.retained:
                entry[0] += 1
        return {section: (kept, total) for section, (kept, total) in counts.items()}

    def by_position(self, buckets: int = 10) -> list[tuple[int, int, int]]:
        if not self.matches or buckets < 1:
            return []
        ordered = sorted(self.matches, key=lambda match: match.rule.position)
        size = math.ceil(len(ordered) / buckets)
        out: list[tuple[int, int, int]] = []
        for index in range(0, len(ordered), size):
            window = ordered[index : index + size]
            out.append((index // size, sum(1 for m in window if m.retained), len(window)))
        return out

    def forgotten(self) -> tuple[Match, ...]:
        return tuple(
            sorted(
                (match for match in self.matches if not match.retained),
                key=lambda match: match.score,
            )
        )


def stem(word: str) -> str:
    if len(word) < 5 or word in _KEEP_WHOLE or not word.isalpha():
        return word
    for suffix in _SUFFIXES:
        if word.endswith(suffix) and len(word) - len(suffix) >= 3:
            return word[: -len(suffix)].rstrip("e")
    return word.rstrip("e") if len(word) >= 6 else word


def content_words(text: str) -> frozenset[str]:
    stripped = _SPLIT.sub(" ", _MARKDOWN.sub(" ", text.lower()))
    return frozenset(stem(word) for word in _WORD.findall(stripped) if word not in _STOPWORDS)


def derive_rules(baseline: str) -> list[Rule]:
    rules: list[Rule] = []
    section = None
    index = 0
    for line in baseline.splitlines():
        if line.startswith("## "):
            section = re.sub(r"[^a-z0-9]+", "-", line[3:].strip().lower()).strip("-")
            index = 0
        elif line.strip().startswith("- ") and section:
            index += 1
            text = re.sub(r"\s+", " ", line.strip()[2:]).strip()
            if content_words(text):
                rules.append(Rule(f"{section}.{index}", section, len(rules), text))
    return rules


def derive_rules_from(path: Path) -> list[Rule]:
    return derive_rules(path.read_text(encoding="utf-8"))


def _idf(rules: list[Rule]) -> dict[str, float]:
    total = len(rules)
    frequency: dict[str, int] = {}
    for rule in rules:
        for word in rule.words:
            frequency[word] = frequency.get(word, 0) + 1
    return {word: math.log(1 + total / count) for word, count in frequency.items()}


def _candidates(response: str) -> list[str]:
    lines = [line.strip() for line in response.splitlines() if content_words(line)]
    windows = list(lines)
    windows.extend(f"{first} {second}" for first, second in pairwise(lines))
    return windows


def _recall(words: frozenset[str], candidate: frozenset[str], idf: dict[str, float]) -> float:
    if not words:
        return 0.0
    total = sum(idf.get(word, 1.0) for word in words)
    if total == 0.0:
        return 0.0
    return sum(idf.get(word, 1.0) for word in words & candidate) / total


def _overlap(rule: Rule, candidate_words: frozenset[str], idf: dict[str, float]) -> float:
    return max(
        _recall(rule.words, candidate_words, idf),
        _recall(rule.imperative_words, candidate_words, idf),
    )


def score_response(rules: list[Rule], response: str) -> Report:
    idf = _idf(rules)
    candidates = _candidates(response)
    prepared = [(candidate, content_words(candidate)) for candidate in candidates]

    matches: list[Match] = []
    for rule in rules:
        best_score = 0.0
        best_evidence = ""
        for candidate, words in prepared:
            value = _overlap(rule, words, idf)
            if value > best_score:
                best_score = value
                best_evidence = candidate
        matches.append(Match(rule, best_score, best_evidence))

    return Report(tuple(matches), len([line for line in response.splitlines() if line.strip()]))

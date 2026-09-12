from __future__ import annotations

import math
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from .stemmer import tokenize

COLLISION_ERROR = 0.75
COLLISION_WARN = 0.50

DEFAULT_TOP_K = 3


def _l2_normalize(vector: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    if norm == 0.0:
        return {}
    return {term: weight / norm for term, weight in vector.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    if len(right) < len(left):
        left, right = right, left
    return sum(weight * right.get(term, 0.0) for term, weight in left.items())


@dataclass(frozen=True)
class Ranking:
    slug: str
    score: float
    rank: int


class Ranker:
    def __init__(self, descriptions: Mapping[str, str]) -> None:
        self.slugs: tuple[str, ...] = tuple(sorted(descriptions))
        counts = {slug: Counter(tokenize(descriptions[slug])) for slug in self.slugs}
        total = len(self.slugs)
        document_frequency = Counter(term for count in counts.values() for term in count)
        self._idf: dict[str, float] = {
            term: math.log(total / df) for term, df in document_frequency.items()
        }
        self._vectors: dict[str, dict[str, float]] = {
            slug: _l2_normalize({
                term: (1.0 + math.log(count)) * self._idf[term]
                for term, count in counts[slug].items()
            })
            for slug in self.slugs
        }

    def _query_vector(self, prompt: str) -> dict[str, float]:
        counts = Counter(term for term in tokenize(prompt) if term in self._idf)
        return _l2_normalize({
            term: (1.0 + math.log(count)) * self._idf[term] for term, count in counts.items()
        })

    def rank(self, prompt: str) -> list[Ranking]:

        query = self._query_vector(prompt)
        scored = sorted(
            ((slug, _cosine(query, self._vectors[slug])) for slug in self.slugs),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [Ranking(slug, score, index + 1) for index, (slug, score) in enumerate(scored)]

    def position(self, prompt: str) -> dict[str, Ranking]:
        return {entry.slug: entry for entry in self.rank(prompt)}

    def pairwise_similarities(self) -> list[tuple[str, str, float]]:
        pairs: list[tuple[str, str, float]] = []
        for i, left in enumerate(self.slugs):
            pairs.extend(
                (left, right, _cosine(self._vectors[left], self._vectors[right]))
                for right in self.slugs[i + 1 :]
            )
        return sorted(pairs, key=lambda item: (-item[2], item[0], item[1]))


@dataclass(frozen=True)
class PositiveCase:
    owner: str
    prompt: str
    top_k: int = DEFAULT_TOP_K


@dataclass(frozen=True)
class NegativeCase:
    entry: str
    prompt: str
    owner: str


@dataclass(frozen=True)
class RoutingReport:
    failures: tuple[str, ...]
    collision_warnings: tuple[str, ...]
    rank1_hits: int
    positives: int

    @property
    def rank1_rate(self) -> float:

        return self.rank1_hits / self.positives if self.positives else 0.0


def _positive_failures(ranker: Ranker, case: PositiveCase) -> tuple[list[str], bool]:
    positions = ranker.position(case.prompt)
    owner = positions.get(case.owner)
    if owner is None:
        return ([f"{case.owner}: positive prompt names an entry outside the ranked catalog"], False)
    if owner.score == 0.0:
        return (
            [
                f"{case.owner}: prompt {case.prompt!r} shares no vocabulary with the "
                "description, so it scores 0 and ranks only by tie-break — the description "
                "is missing words a user actually says"
            ],
            False,
        )
    if owner.rank > case.top_k:
        ahead = ", ".join(
            f"{entry.slug} ({entry.score:.3f})" for entry in ranker.rank(case.prompt)[: case.top_k]
        )
        return (
            [
                f"{case.owner}: prompt {case.prompt!r} ranks it {owner.rank}, outside "
                f"top-{case.top_k} — ahead of it: {ahead}"
            ],
            False,
        )
    return ([], owner.rank == 1)


def _negative_failures(ranker: Ranker, case: NegativeCase) -> list[str]:
    positions = ranker.position(case.prompt)
    owner = positions.get(case.owner)
    entry = positions.get(case.entry)
    if owner is None or entry is None:
        missing = case.owner if owner is None else case.entry
        return [f"{case.entry}: negative prompt names '{missing}', which is not a ranked entry"]
    if owner.score == 0.0:
        return [
            f"{case.entry}: negative prompt {case.prompt!r} scores 0 for its declared owner "
            f"'{case.owner}', so it matches nothing and proves nothing — write a prompt the "
            "owner actually answers"
        ]
    if owner.rank >= entry.rank:
        return [
            f"{case.entry}: negative prompt {case.prompt!r} ranks it {entry.rank} "
            f"({entry.score:.3f}), at or above its declared owner '{case.owner}' at "
            f"{owner.rank} ({owner.score:.3f})"
        ]
    return []


def _collision_findings(ranker: Ranker) -> tuple[list[str], list[str]]:
    failures: list[str] = []
    warnings: list[str] = []
    for left, right, score in ranker.pairwise_similarities():
        if score >= COLLISION_ERROR:
            failures.append(
                f"{left} and {right}: descriptions are {score:.0%} similar (ceiling "
                f"{COLLISION_ERROR:.0%}) — an agent cannot route between them; give each "
                "the vocabulary only it should answer to"
            )
        elif score >= COLLISION_WARN:
            warnings.append(
                f"{left} and {right}: descriptions are {score:.0%} similar (warning at "
                f"{COLLISION_WARN:.0%})"
            )
    return failures, warnings


def evaluate(
    descriptions: Mapping[str, str],
    positives: Iterable[PositiveCase],
    negatives: Iterable[NegativeCase],
) -> RoutingReport:

    ranker = Ranker(descriptions)
    failures, warnings = _collision_findings(ranker)

    hits = 0
    positive_cases = sorted(positives, key=lambda case: (case.owner, case.prompt))
    for case in positive_cases:
        case_failures, ranked_first = _positive_failures(ranker, case)
        failures.extend(case_failures)
        hits += int(ranked_first)

    for negative in sorted(negatives, key=lambda case: (case.entry, case.prompt)):
        failures.extend(_negative_failures(ranker, negative))

    return RoutingReport(
        failures=tuple(failures),
        collision_warnings=tuple(warnings),
        rank1_hits=hits,
        positives=len(positive_cases),
    )


def floor_violations(rate: float, floor: float | None, high_water: float | None) -> list[str]:

    if floor is None:
        return [
            "no rank-1 floor declared — set `[catalog] rank1_floor` in basicly.toml "
            f"below the measured baseline (currently {rate:.1%}). It is a fraction "
            f"between 0 and 1, not a percentage: write {max(rate - 0.02, 0.0):.2f}, "
            f"not {rate * 100:.1f}"
        ]
    violations: list[str] = []
    if high_water is not None and floor < high_water:
        violations.append(
            f"[catalog] rank1_floor {floor:.1%} is below rank1_floor_high_water "
            f"{high_water:.1%} — a rank-1 floor may be raised, never lowered. Lowering it "
            "to make a regression pass is deleting the test while looking like maintenance; "
            "fix the description the eval is failing on instead"
        )
    if rate < floor:
        violations.append(
            f"rank-1 rate {rate:.1%} is below the declared floor {floor:.1%} — routing "
            "regressed. Fix the descriptions the positive prompts miss; do not lower the floor"
        )
    return violations


def entry_cases(
    slug: str, data: Mapping[str, object]
) -> tuple[list[PositiveCase], list[NegativeCase]]:

    trigger = data.get("trigger")
    if not isinstance(trigger, Mapping):
        return [], []

    positives: list[PositiveCase] = []
    for case in _mappings(trigger.get("positive")):
        prompt = _text(case, "prompt")
        top_k = case.get("top_k", DEFAULT_TOP_K)
        if prompt is not None and isinstance(top_k, int) and not isinstance(top_k, bool):
            positives.append(PositiveCase(owner=slug, prompt=prompt, top_k=top_k))

    negatives: list[NegativeCase] = []
    for case in _mappings(trigger.get("negative")):
        prompt, owner = _text(case, "prompt"), _text(case, "owner")
        if prompt is not None and owner is not None:
            negatives.append(NegativeCase(entry=slug, prompt=prompt, owner=owner))

    return positives, negatives


def _mappings(raw: object) -> list[Mapping[str, object]]:
    return [case for case in raw if isinstance(case, Mapping)] if isinstance(raw, list) else []


def _text(case: Mapping[str, object], field: str) -> str | None:
    value = case.get(field)
    return value if isinstance(value, str) and value.strip() else None

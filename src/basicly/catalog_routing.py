"""Tier-2 routing evals — deterministic lexical ranking over entry descriptions.

Where ``catalog_lint`` guards the source contract (Tier 1: is the entry
well-formed?) this module answers Tier 2: *does the entry fire when it should,
and only then?* (``docs/design/catalog-efficacy-design.md`` §3). It is pure
computation over strings — the caller reads the catalog off disk and hands the
descriptions and the eval cases in, so nothing here touches the filesystem and
the whole tier is testable from a dict.

Three properties are load-bearing, and one temptation is refused:

* **Deterministic.** Same corpus in, same ranking out, on every host and every
  run. Ties break on the slug, never on dict order, so a rank is a fact about
  the catalog rather than about the insertion order of a mapping.
* **Pure Python, no new runtime dependency.** A stemmer and a TF-IDF ranker are
  a small amount of code we can own, and owning them is why this gate is free
  and always runs.
* **No embeddings.** They would make Tier 2 semantic, and therefore better at
  judging relevance — and also non-deterministic, network-dependent and
  unownable. Semantics are Tier 3's job.

The stemmer is a deliberately conservative subset of Porter's step 1 (plurals,
``-ed``/``-ing``, ``-ly``) rather than the full algorithm: those are the endings
that actually separate a user's phrasing from a description's ("commits" vs
"commit", "rendering" vs "render"), and every derivational rule beyond them buys
less conflation than it risks. The rule table below *is* the specification —
this is our stemmer, not a claim about someone else's.
"""

from __future__ import annotations

import math
import re
from collections import Counter
from collections.abc import Iterable, Mapping
from dataclasses import dataclass

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

# Collision ceilings (§3.1 assertion 3): a pair at or above the error ceiling is
# a lint violation, a pair at or above the warning ceiling is an advisory.
COLLISION_ERROR = 0.75
COLLISION_WARN = 0.50

# Default top-k for a positive routing assertion; an entry's signature ask
# declares `top_k: 1` in its own case file.
DEFAULT_TOP_K = 3


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


def _restore(stem: str) -> str:
    """Repair the stem left by an ``-ed``/``-ing`` strip.

    Three repairs, in order: a stem ending "at"/"bl"/"iz" regains its "e"
    ("conflat" -> "conflate"); a doubled final consonant that is not l/s/z is
    undoubled ("hopp" -> "hop"); a short consonant-vowel-consonant stem regains
    its "e" ("hop" from "hoping" -> "hope").
    """
    if stem.endswith(("at", "bl", "iz")):
        return stem + "e"
    if _ends_double_consonant(stem) and stem[-1] not in "lsz":
        return stem[:-1]
    if _measure(stem) == 1 and _ends_cvc(stem):
        return stem + "e"
    return stem


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


def _l2_normalize(vector: dict[str, float]) -> dict[str, float]:
    norm = math.sqrt(sum(weight * weight for weight in vector.values()))
    if norm == 0.0:
        return {}
    return {term: weight / norm for term, weight in vector.items()}


def _cosine(left: Mapping[str, float], right: Mapping[str, float]) -> float:
    """Dot product of two already-L2-normalized sparse vectors."""
    if len(right) < len(left):
        left, right = right, left
    return sum(weight * right.get(term, 0.0) for term, weight in left.items())


@dataclass(frozen=True)
class Ranking:
    """One entry's position in a ranking, and the score that put it there."""

    slug: str
    score: float
    rank: int


class Ranker:
    """Stemmed TF-IDF over a corpus of entry descriptions.

    ``tf`` is sublinear (``1 + log(count)``) and ``idf`` is ``log(N / df)``, so a
    term carried by *every* description contributes exactly nothing — which is
    the property that keeps "Use ... when ..." boilerplate out of the ranking
    without hand-maintaining a catalog-specific stop list. Both document and
    query vectors are L2-normalized, so a score is a cosine in ``[0, 1]``.
    """

    def __init__(self, descriptions: Mapping[str, str]) -> None:
        """Index ``descriptions`` (slug -> description text) for ranking."""
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
        """Rank every entry against ``prompt``, best first.

        Ties break on the slug so the ordering is total and reproducible; a
        score of exactly zero means the prompt shares no discriminating
        vocabulary with the entry at all, which callers must treat as "no
        evidence" rather than as a position.
        """
        query = self._query_vector(prompt)
        scored = sorted(
            ((slug, _cosine(query, self._vectors[slug])) for slug in self.slugs),
            key=lambda pair: (-pair[1], pair[0]),
        )
        return [Ranking(slug, score, index + 1) for index, (slug, score) in enumerate(scored)]

    def position(self, prompt: str) -> dict[str, Ranking]:
        """``rank`` keyed by slug, for callers asking about specific entries."""
        return {entry.slug: entry for entry in self.rank(prompt)}

    def pairwise_similarities(self) -> list[tuple[str, str, float]]:
        """Every description pair with its cosine similarity, most similar first."""
        pairs: list[tuple[str, str, float]] = []
        for i, left in enumerate(self.slugs):
            for right in self.slugs[i + 1 :]:
                pairs.append((left, right, _cosine(self._vectors[left], self._vectors[right])))
        return sorted(pairs, key=lambda item: (-item[2], item[0], item[1]))


@dataclass(frozen=True)
class PositiveCase:
    """A realistic prompt whose owning entry must rank in the top ``top_k``."""

    owner: str
    prompt: str
    top_k: int = DEFAULT_TOP_K


@dataclass(frozen=True)
class NegativeCase:
    """A prompt belonging to ``owner``, asserted to outrank ``entry``.

    The stronger pairwise form, and the reason it is stronger is decisive: a
    bare "``entry`` must not rank first" passes vacuously whenever the prompt
    matches nothing at all.
    """

    entry: str
    prompt: str
    owner: str


@dataclass(frozen=True)
class RoutingReport:
    """The outcome of one Tier-2 run over a catalog."""

    failures: tuple[str, ...]
    collision_warnings: tuple[str, ...]
    rank1_hits: int
    positives: int

    @property
    def rank1_rate(self) -> float:
        """Share of positive prompts whose owner ranked *first*, not merely top-k.

        Zero positives yields ``0.0``: an empty corpus has not demonstrated
        routing, and reporting it as a perfect score would make the CI floor
        pass on a catalog with no evidence in it at all.
        """
        return self.rank1_hits / self.positives if self.positives else 0.0


def _positive_failures(ranker: Ranker, case: PositiveCase) -> tuple[list[str], bool]:
    """Failures for one positive case, plus whether its owner ranked first."""
    positions = ranker.position(case.prompt)
    owner = positions.get(case.owner)
    if owner is None:
        return ([f"{case.owner}: positive prompt names an entry outside the ranked catalog"], False)
    if owner.score == 0.0:
        # Anti-vacuity: an entry can only be "ranked" by a prompt that shares
        # vocabulary with it. Without this, an all-zero prompt hands rank 1 to
        # whichever slug sorts first and the assertion passes having measured
        # nothing.
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
    """Failures for one negative case: its owner must outrank the entry."""
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
    """Split description pairs into over-ceiling failures and warnings."""
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
    """Run the three Tier-2 assertions over ``descriptions`` and report rank-1 rate.

    A Tier-2 failure means *fix the description, not the eval*. If a realistic
    prompt cannot rank its entry, the description is missing vocabulary a user
    actually says, and that is a real finding about a real defect.
    """
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
    """Check the measured rank-1 ``rate`` against the declared CI floor.

    Two rules, and the second is the one that matters. The rate must clear the
    floor. And the floor must never be *lowered*: ``high_water`` records the
    highest floor this repo has ever committed to, so relaxing the threshold to
    make a regression pass has to relax the record too — which turns a change
    that reads like maintenance into a diff that states what it is. Lowering a
    floor is the same act as deleting the test.
    """
    if floor is None:
        return [
            "no rank-1 floor declared — set `[catalog] rank1_floor` in basicly.toml "
            f"below the measured baseline (currently {rate:.1%})"
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
    """Split one loaded ``evals.yaml`` document into its positive/negative cases.

    Shape errors are not reported here — the JSON Schema owns that — so anything
    malformed is skipped rather than crashing the gate that is about to name it.
    """
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

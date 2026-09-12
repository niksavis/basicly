from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

REVIEW_TASK = (
    "You are giving an advisory semantic review of generated agent-instruction "
    "files. The deterministic checks (schema, duplicate bodies, and static "
    "contradiction / ambiguity / scope detection) have already passed — do not "
    "repeat them. Find only what they cannot: guidance in one section that "
    "contradicts another, instructions ambiguous enough that an agent could act "
    "on them two different ways, and redundancy that wastes the context budget. "
    "Report each finding with the file name and the exact quoted text; if you "
    "find nothing, say so plainly. This is advisory only — do not modify any "
    "files."
)


_PRE_JUDGING_PATTERNS: tuple[re.Pattern[str], ...] = (
    re.compile(
        r"\b(?:do not|don'?t|never|avoid)\s+(?:flag|report|raise|surface)(?:ing)?\b",
        re.IGNORECASE,
    ),
    re.compile(
        r"\b(?:do not|don'?t|never)\s+treat\b[^.\n]{0,80}?\bas\s+(?:an?\s+)?"
        r"(?:defect|bug|finding|issue|problem|violation)\b",
        re.IGNORECASE,
    ),
    re.compile(r"\bat\s+most\s+(?:an?\s+)?(?:minor|low|nit|informational)\b", re.IGNORECASE),
    re.compile(
        r"\bthe\s+(?:plan|design|spec|architect)\s+(?:already\s+)?(?:chose|decided|accepted)\b",
        re.IGNORECASE,
    ),
)


class PreJudgingError(ValueError):
    def __init__(self, matches: Sequence[str]) -> None:
        self.matches = tuple(matches)
        quoted = ", ".join(repr(match) for match in self.matches)
        super().__init__(
            f"refusing to emit a reviewer bundle that pre-judges the review: {quoted} — "
            "let the reviewer raise the finding and adjudicate it instead"
        )


def find_pre_judging(text: str) -> tuple[str, ...]:

    found: dict[str, None] = {}
    for pattern in _PRE_JUDGING_PATTERNS:
        for match in pattern.finditer(text):
            found.setdefault(match.group(0), None)
    return tuple(found)


def reject_pre_judging(text: str) -> None:
    matches = find_pre_judging(text)
    if matches:
        raise PreJudgingError(matches)


@dataclass(frozen=True)
class ReviewMaterial:
    label: str
    content: str


def build_review_prompt(materials: list[ReviewMaterial]) -> str:

    count = len(materials)
    noun, verb = ("file", "is") if count == 1 else ("files", "are")
    sections = [f"===== FILE: {material.label} =====\n{material.content}" for material in materials]
    body = "\n\n".join(sections)
    framing = f"The following {count} generated {noun} {verb} under review."
    prompt = f"{REVIEW_TASK}\n\n{framing}\n\n{body}"
    reject_pre_judging(prompt)
    return prompt

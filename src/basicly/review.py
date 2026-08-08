"""Advisory agent-assisted semantic review of the rendered files (basicly-qps).

This is the *second*, advisory layer of the verification pipeline (§6, §11.5).
The deterministic gate (`catalog verify`: schema, duplicate bodies, static
contradiction/ambiguity/scope) runs first and blocks; this layer asks an agent to
read the rendered always-on files and report only what the static checks cannot
catch — contradictions between sections, genuinely ambiguous instructions, and
context-bloating redundancy. It is a report, never a merge gate: the caller
always exits 0 (§3.3 deterministic-first, semantic-second; §12.4 semantic review
is a non-required gate).

This module is pure: it turns rendered material into a review prompt. Loading,
rendering, and dispatching the prompt to a runner is the CLI's job, so the prompt
assembly stays unit-testable without an agent on PATH.

It also owns the **no-pre-judging lint**, which
every reviewer bundle this repo assembles must pass before it is emitted.
"""

from __future__ import annotations

import re
from collections.abc import Sequence
from dataclasses import dataclass

# The semantic-review task. It names the layer's job precisely so the agent does
# not re-run the deterministic checks that already passed, and forbids edits so
# the pass stays advisory.
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


# --- The no-pre-judging lint ---------------

# superpowers states the rule as a string test the prompt's *author* applies to
# themselves: if what you are writing says "do not flag", "don't treat X as a
# defect", "at most Minor", or "the plan chose" — stop, you are pre-judging,
# usually to spare yourself a review loop. Because our reviewer bundles are
# assembled by code, we can do better than intent and check the emitted bundle.
#
# The vocabulary is deliberately the four phrases §5.3 names plus their obvious
# morphological variants, and nothing invented. A lint that fires on ordinary
# prose gets suppressed, which costs more than the rule buys; each pattern here
# needs an explicit imperative aimed at the reviewer's *output*, so a document
# that merely discusses defects does not trip it.
_PRE_JUDGING_PATTERNS: tuple[re.Pattern[str], ...] = (
    # "do not flag" — and the other verbs for the same instruction.
    re.compile(
        r"\b(?:do not|don'?t|never|avoid)\s+(?:flag|report|raise|surface)(?:ing)?\b",
        re.IGNORECASE,
    ),
    # "don't treat X as a defect", for a short X on one sentence.
    re.compile(
        r"\b(?:do not|don'?t|never)\s+treat\b[^.\n]{0,80}?\bas\s+(?:an?\s+)?"
        r"(?:defect|bug|finding|issue|problem|violation)\b",
        re.IGNORECASE,
    ),
    # "at most Minor" — a severity ceiling set before anything was looked at.
    re.compile(r"\bat\s+most\s+(?:an?\s+)?(?:minor|low|nit|informational)\b", re.IGNORECASE),
    # "the plan chose" — deciding the adjudication in the prompt that asks for it.
    re.compile(
        r"\bthe\s+(?:plan|design|spec|architect)\s+(?:already\s+)?(?:chose|decided|accepted)\b",
        re.IGNORECASE,
    ),
)


class PreJudgingError(ValueError):
    """A reviewer bundle carrying a finding-suppressing directive.

    Raised instead of returning the bundle, because the engine's job here is to
    **refuse to emit** it: a bundle that tells the reviewer what not to find is
    not a weaker review, it is a review whose result is already written.
    """

    def __init__(self, matches: Sequence[str]) -> None:
        """Refuse the bundle, naming each directive so the author can delete it."""
        self.matches = tuple(matches)
        quoted = ", ".join(repr(match) for match in self.matches)
        super().__init__(
            f"refusing to emit a reviewer bundle that pre-judges the review: {quoted} — "
            "let the reviewer raise the finding and adjudicate it instead"
        )


def find_pre_judging(text: str) -> tuple[str, ...]:
    """Every finding-suppressing directive in *text*, in order of appearance.

    Returns the matched phrases themselves (deduplicated, case preserved) so a
    caller can name what it refused rather than only that it refused.
    """
    found: dict[str, None] = {}
    for pattern in _PRE_JUDGING_PATTERNS:
        for match in pattern.finditer(text):
            found.setdefault(match.group(0), None)
    return tuple(found)


def reject_pre_judging(text: str) -> None:
    """Raise :class:`PreJudgingError` when *text* pre-judges the review it asks for."""
    matches = find_pre_judging(text)
    if matches:
        raise PreJudgingError(matches)


@dataclass(frozen=True)
class ReviewMaterial:
    """One rendered file to put in front of the reviewer."""

    label: str
    content: str


def build_review_prompt(materials: list[ReviewMaterial]) -> str:
    """Assemble the deterministic review prompt: task text plus every rendered file.

    Files are emitted in the order given, each under a clearly delimited header so
    the agent can attribute a finding to a specific file. Output is a pure function
    of the inputs, so the same catalog always yields the same prompt.

    The whole assembled bundle is linted, material included, not just the task
    text this module owns — the reviewer reads one prompt and cannot tell which
    span was instruction and which was evidence, so a suppressing directive
    inside a rendered file suppresses findings exactly as well as one in the
    task. Raises :class:`PreJudgingError` rather than emitting it.
    """
    count = len(materials)
    noun, verb = ("file", "is") if count == 1 else ("files", "are")
    sections = [f"===== FILE: {material.label} =====\n{material.content}" for material in materials]
    body = "\n\n".join(sections)
    framing = f"The following {count} generated {noun} {verb} under review."
    prompt = f"{REVIEW_TASK}\n\n{framing}\n\n{body}"
    reject_pre_judging(prompt)
    return prompt

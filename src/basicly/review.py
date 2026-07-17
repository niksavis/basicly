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
"""

from __future__ import annotations

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
    """
    count = len(materials)
    noun, verb = ("file", "is") if count == 1 else ("files", "are")
    sections = [f"===== FILE: {material.label} =====\n{material.content}" for material in materials]
    body = "\n\n".join(sections)
    framing = f"The following {count} generated {noun} {verb} under review."
    return f"{REVIEW_TASK}\n\n{framing}\n\n{body}"

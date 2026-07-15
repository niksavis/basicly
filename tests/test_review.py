"""Tests for the advisory semantic-review prompt builder (basicly-qps).

The builder is pure: rendered material in, a deterministic prompt out. These
pin that the task text is present, every file is delimited and attributable, and
the same input always yields the same prompt (advisory layer, no agent needed).
"""

from __future__ import annotations

from basicly.review import REVIEW_TASK, ReviewMaterial, build_review_prompt


def test_prompt_includes_task_and_every_file() -> None:
    """The prompt leads with the task text and embeds each file under its label."""
    materials = [
        ReviewMaterial("AGENTS.md", "agents body"),
        ReviewMaterial("CLAUDE.md", "claude body"),
    ]
    prompt = build_review_prompt(materials)
    assert prompt.startswith(REVIEW_TASK)
    assert "2 generated files are under review" in prompt
    assert "===== FILE: AGENTS.md =====\nagents body" in prompt
    assert "===== FILE: CLAUDE.md =====\nclaude body" in prompt


def test_prompt_singular_noun_for_one_file() -> None:
    """A single file uses the singular noun in the framing sentence."""
    prompt = build_review_prompt([ReviewMaterial("AGENTS.md", "body")])
    assert "1 generated file is under review" in prompt


def test_prompt_preserves_material_order() -> None:
    """Files appear in the order given so findings stay attributable."""
    prompt = build_review_prompt([
        ReviewMaterial("first.md", "x"),
        ReviewMaterial("second.md", "y"),
    ])
    assert prompt.index("first.md") < prompt.index("second.md")


def test_prompt_is_deterministic() -> None:
    """Identical material yields a byte-identical prompt."""
    materials = [ReviewMaterial("a.md", "one"), ReviewMaterial("b.md", "two")]
    assert build_review_prompt(materials) == build_review_prompt(materials)

from __future__ import annotations

import re
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).parent.parent
SKILLS = ROOT / ".basicly" / "core" / "skills"
DESTRUCTIVE = (
    "Never clear another session's files",
    "Never signal a supervisor",
)
HIGH_EFFORT = (
    "root-cause",
    "falsify-first",
    "repair-in-place",
    "validate-as-consumer",
    "interface-facts",
    "decompose-plan",
    "best-practices-audit",
)
DESCRIPTIVE_NEVER = (
    "loop never exits and reports a finished job as still running",
    "It never blocks",
    "cites a finding but never links it to another",
)
SENTENCE = re.compile(r"(?<=[.!?])\s+|\n")
NEVER = re.compile(r"\bnever\b", re.IGNORECASE)


def _source(slug: str) -> dict[str, object]:
    return yaml.safe_load((SKILLS / slug / "skill.yaml").read_text(encoding="utf-8"))


def _text(slug: str) -> str:
    parts = [str(_source(slug)["instructions"])]
    parts += [
        path.read_text(encoding="utf-8")
        for path in sorted((SKILLS / slug / "references").glob("*.md"))
    ]
    return re.sub(r"[ \t]+", " ", "\n".join(parts).replace("**", ""))


def test_harness_loop_keeps_its_destructive_rules() -> None:
    text = _text("harness-loop")
    missing = [rule for rule in DESTRUCTIVE if rule not in text]
    assert not missing, missing


def test_swarm_keeps_the_general_purpose_model_rule() -> None:
    assert (
        "For a general-purpose agent, pass `model`, because the tier hook denies that call "
        "without it" in _text("swarm")
    )


def test_operations_points_delegation_to_the_always_on_rule() -> None:
    operations = (SKILLS / "harness-loop" / "references" / "operations.md").read_text(
        encoding="utf-8"
    )
    for rule in (
        "Delegate only by the always-on Harness Loop rule",
        "Start a fresh context for unrelated work and pass the brief",
        "Resume a saved context only for the same task",
    ):
        assert rule in operations, rule


@pytest.mark.parametrize("slug", ["harness-loop", "swarm"])
def test_every_never_is_destructive_or_descriptive(slug: str) -> None:
    sentences = [part.strip().removeprefix("- ") for part in SENTENCE.split(_text(slug))]
    stray = [
        sentence
        for sentence in sentences
        if NEVER.search(sentence)
        and not sentence.startswith(DESTRUCTIVE)
        and not any(known in sentence for known in DESCRIPTIVE_NEVER)
    ]
    assert not stray, f"{slug} has a never rule off the destructive list: {stray}"


def test_the_skill_review_checklist_asks_for_one_short_reason_per_rule() -> None:
    checklist = SKILLS / "best-practices-audit" / "references" / "checklist.md"
    assert "one short reason" in checklist.read_text(encoding="utf-8")


@pytest.mark.parametrize("slug", HIGH_EFFORT)
def test_verification_skills_pin_high_effort(slug: str) -> None:
    claude = _source(slug)["claude"]
    assert isinstance(claude, dict)
    assert claude["effort"] == "high"
    projected = (ROOT / ".claude" / "skills" / slug / "SKILL.md").read_text(encoding="utf-8")
    assert "\neffort: high\n" in projected.split("\n---\n", 1)[0] + "\n"

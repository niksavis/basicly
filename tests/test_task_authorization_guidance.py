from __future__ import annotations

import re
from pathlib import Path

import pytest

ROOT = Path(__file__).parent.parent


@pytest.mark.parametrize(
    "path", ["AGENTS.md", ".claude/CLAUDE.md", ".github/copilot-instructions.md"]
)
def test_baselines_authorize_the_task_and_preserve_real_protections(path: str) -> None:
    text = (ROOT / path).read_text(encoding="utf-8")
    section = re.split(r"\n#{2,3} ", text.split("### Harness Loop\n", 1)[1], maxsplit=1)[0]
    assert "user task request authorizes its scope" in section
    assert "Do not request an L-level grant or mandatory token budget" in section
    assert "verification gates and destructive-action protections" in section
    assert "Honor limits the user explicitly sets" in section
    assert "never defeat a gate" in text


@pytest.mark.parametrize("root", [".agents/skills", ".claude/skills"])
def test_loop_skill_does_not_reintroduce_grant_ceremonies(root: str) -> None:
    text = (ROOT / root / "harness-loop/SKILL.md").read_text(encoding="utf-8")
    assert "task request authorizes classification, decomposition and completion" in text
    assert "Do not create a synthetic grant" in text
    assert "basicly policy grant" not in text
    assert "Get an explicit yes before you relay a confirm code" not in text
    assert "basicly loop kill <id>" in text
    assert "--confirm <code>" in text
    assert "equivalent trusted inputs" in text


@pytest.mark.parametrize("root", [".agents/skills", ".claude/skills"])
def test_plan_guidance_keeps_spending_limits_optional(root: str) -> None:
    text = (ROOT / root / "decompose-plan/SKILL.md").read_text(encoding="utf-8")
    assert "Spending limits and historical integrity fields are optional" in text
    assert "Omit it when the user sets no limit" in text
    assert "`budget_tokens` | absent" not in text
    assert "`integrity` | absent" not in text
    assert "`demonstration` | absent" in text


@pytest.mark.parametrize("root", [".agents/skills", ".claude/skills"])
def test_release_guidance_requires_gates_without_a_permission_ladder(root: str) -> None:
    text = (ROOT / root / "release-process/SKILL.md").read_text(encoding="utf-8")
    assert "user release request authorizes the requested cut and publication" in text
    assert "Required release gates must still pass" in text
    assert "green L3 grant" not in text


def test_optional_budget_command_is_documented_without_grants() -> None:
    text = (ROOT / ".basicly/core/skills/harness-loop/references/many-lanes.md").read_text(
        encoding="utf-8"
    )
    assert "basicly policy budget <epic> --tokens N" in text
    assert "basicly policy budget <epic> --clear" in text
    assert "basicly policy grant" not in text
